"use strict";
// Part of the UI, loads after app.js; shared global scope, order defined in index.html
// ---- Edit configuration -------------------------------------------------------------------
// "Edit" per card turns the writable fields (allow list app/writable.py) into inputs. Changes collect
// as a draft; "Review & write" shows old → new and the exact calls from the server, only then it is written.

let cfgEdit = { id: null, open: new Set(), draft: {}, errors: {}, review: null, busy: false, notice: null };

function cfgReset(id) { cfgEdit = { id, open: new Set(), draft: {}, errors: {}, review: null, busy: false, notice: null }; }
const cfgHas = (path) => cfgEdit.id === devState.id && Object.prototype.hasOwnProperty.call(cfgEdit.draft, path);
const cfgDirty = () => cfgEdit.busy || (cfgEdit.id === devState.id && (Object.keys(cfgEdit.draft).length > 0 || !!cfgEdit.review));
const cfgSame = (a, b) => (typeof a === "number" && typeof b === "number" ? Math.abs(a - b) < 1e-9 : JSON.stringify(a) === JSON.stringify(b));

function cfgFields() {
  const out = {};
  for (const sec of devState.body?.settings?.sections || []) for (const f of sec.fields) if (f.editable) out[f.path] = f;
  return out;
}

function cfgInput(f) {
  const v = cfgHas(f.path) ? cfgEdit.draft[f.path] : f.value;
  const attr = `data-cfg="${esc(f.path)}" aria-label="${esc(f.label)}"${cfgEdit.errors[f.path] ? ` aria-invalid="true"` : ""}`;
  const bad = cfgEdit.errors[f.path] ? " is-invalid" : "";
  if (f.kind === "bool") {
    return `<label class="form-check form-switch m-0 d-inline-flex justify-content-end"><input class="form-check-input${bad}" type="checkbox" ${attr}${v ? " checked" : ""}></label>`;
  }
  if (f.kind === "enum") {
    const opts = [...(f.options || [])];
    if (!opts.some((o) => cfgSame(o, f.value))) opts.unshift(f.value);
    return `<select class="form-select form-select-sm rr-cfg-in${bad}" ${attr}>${opts.map((o) =>
      `<option value="${esc(JSON.stringify(o))}"${cfgSame(o, v) ? " selected" : ""}>${esc(o === null ? "none" : o)}</option>`).join("")}</select>`;
  }
  if (f.kind === "number") {
    const lim = `${f.min !== undefined ? ` min="${esc(f.min)}"` : ` min="0"`}${f.max !== undefined ? ` max="${esc(f.max)}"` : ""}`;
    const input = `<input class="form-control${bad}" type="number" step="any" inputmode="decimal"${lim} value="${esc(v ?? "")}" ${attr}>`;
    return `<div class="input-group input-group-sm rr-cfg-in ms-auto">${input}${f.unit ? `<span class="input-group-text">${esc(f.unit)}</span>` : ""}</div>`;
  }
  return `<input class="form-control form-control-sm rr-cfg-in ms-auto${bad}" type="text" maxlength="255" value="${esc(v ?? "")}"${v === null ? ` placeholder="none"` : ""} ${attr}>`;
}

const cfgUseBtn = (f, value, title) => `<button class="btn btn-sm btn-ghost-primary" type="button" data-cfg-use="${esc(f.path)}"
    data-value="${esc(JSON.stringify(value))}" title="${esc(title)}">Use ${esc(fmtVal(value))}${f.unit && typeof value === "number" ? ` ${esc(f.unit)}` : ""}</button>`;

// Value from the input field; undefined = invalid (e.g. empty number)
function cfgRead(el, f) {
  if (f.kind === "bool") return el.checked;
  if (f.kind === "enum") return JSON.parse(el.value);
  if (f.kind === "number") return el.value.trim() === "" || !Number.isFinite(Number(el.value)) ? undefined : Number(el.value);
  return el.value === "" && f.value === null ? null : el.value;
}

function cfgSet(path, value) {
  const f = cfgFields()[path];
  if (!f) return;
  cfgEdit.id = devState.id;
  delete cfgEdit.errors[path];
  if (value !== undefined && cfgSame(value, f.value)) delete cfgEdit.draft[path];
  else cfgEdit.draft[path] = value === undefined ? null : value;
  if (value === undefined) cfgEdit.errors[path] = "Enter a number";
  cfgEdit.review = null;
  cfgEdit.notice = null;
  const row = $(`[data-cfg-row="${CSS.escape(path)}"]`);
  row?.classList.toggle("rr-changed", cfgHas(path));
  const input = row && $("[data-cfg]", row);
  if (input) {
    input.classList.toggle("is-invalid", !!cfgEdit.errors[path]);
    $(".invalid-feedback", row)?.remove();
  }
  renderCfgBar();
}

function cfgBarHtml() {
  if (cfgEdit.id !== devState.id) return "";
  const r = cfgEdit.review;
  if (r) {
    const rows = r.rows.map((x) => `<tr><td>${esc(x.label)}<span class="rr-sub">${esc(x.path)}</span></td>
        <td class="rr-old">${diffVal(x.old)}</td><td class="rr-new">${diffVal(x.new)}${x.unit && typeof x.new === "number" ? ` ${esc(x.unit)}` : ""}</td></tr>`).join("");
    const calls = r.calls.map((c) => `${c.method} ${JSON.stringify(c.params)}`).join("\n");
    return `<div class="card mb-3 border-primary"><div class="card-header"><h3 class="card-title">Review ${r.rows.length} change${r.rows.length === 1 ? "" : "s"}</h3></div>
      ${cardTable(["Setting", "Current", "New"], rows)}
      <div class="card-body border-top"><div class="text-secondary small mb-1">RelayRoster sends exactly these calls, then reads the device again and checks every value:</div>
        <pre class="rr-json mb-0" id="cfgCalls">${esc(calls)}</pre></div>
      <div class="card-footer d-flex flex-wrap gap-2 justify-content-end">
        <button class="btn" type="button" data-cfg-back${cfgEdit.busy ? " disabled" : ""}>Back to editing</button>
        <button class="btn btn-primary" type="button" data-cfg-write${cfgEdit.busy ? " disabled" : ""}>${cfgEdit.busy ? "Writing …" : "Write to device"}</button></div></div>`;
  }
  const n = Object.keys(cfgEdit.draft).length;
  const notice = cfgEdit.notice ? `<div class="alert alert-${cfgEdit.notice.kind} mb-3" role="status">${cfgEdit.notice.html}</div>` : "";
  if (!n) return notice;
  const bad = Object.keys(cfgEdit.errors).length;
  return `${notice}<div class="card card-sm mb-3 border-primary"><div class="card-body d-flex flex-wrap align-items-center gap-2">
      <div class="me-auto"><strong>Pending changes (${n})</strong><span class="rr-sub">${esc(Object.keys(cfgEdit.draft).sort().join(", "))}</span></div>
      <button class="btn" type="button" data-cfg-discard>Discard</button>
      <button class="btn btn-primary" type="button" data-cfg-review${bad ? ` disabled title="Fix the highlighted fields first"` : ""}>Review &amp; write</button></div></div>`;
}

function renderCfgBar() {
  const bar = $("#cfgBar");
  if (bar) bar.innerHTML = cfgBarHtml();
}

async function cfgReview() {
  const id = devState.id;
  const { status, body } = await api(`api/devices/${encodeURIComponent(id)}/config/preview`, { method: "POST", json: { changes: cfgEdit.draft } });
  if (devState.id !== id) return;
  if (status === 422) {
    cfgEdit.errors = body.errors || {};
    for (const path of Object.keys(cfgEdit.errors)) cfgEdit.open.add(path.split(".")[0]);
    renderDeviceTab();
    return;
  }
  if (!body.rows.length) {
    cfgEdit.draft = {};
    cfgEdit.notice = { kind: "info", html: "Nothing to write: all values are the same as on the device." };
    renderDeviceTab();
    return;
  }
  cfgEdit.review = body;
  renderCfgBar();
  $("#cfgBar")?.scrollIntoView?.({ block: "nearest" });
}

async function cfgWrite() {
  const id = devState.id, r = cfgEdit.review;
  if (!r || cfgEdit.busy) return;
  cfgEdit.busy = true;
  renderCfgBar();
  devState.hold += 1;
  let status, body;
  try {
    ({ status, body } = await api(`api/devices/${encodeURIComponent(id)}/config`, { method: "POST", json: { changes: r.changes }, keep: true }));
  } catch (e) {
    cfgEdit.busy = false;
    devState.hold -= 1;
    cfgEdit.review = null;
    cfgEdit.notice = { kind: "danger", html: esc(e.message) };
    renderCfgBar();
    return;
  }
  cfgEdit.busy = false;
  if (status === 422) {
    devState.hold -= 1;
    cfgEdit.review = null;
    cfgEdit.errors = body.errors || {};
    renderDeviceTab();
    return;
  }
  const restart = body.restart_required
    ? allowReboot
      ? ` The device needs a restart for some of the changes. <button class="btn btn-sm ms-2" type="button" data-act="reboot">${ICON.power}Restart now</button>`
      : ` The device needs a restart for some of the changes. Restart it from its web UI, or turn on <a href="#settings">Allow restarting devices</a>.`
    : "";
  const notice = body.ok
    ? { kind: "success", html: `Written and confirmed: ${body.results.length} setting${body.results.length === 1 ? "" : "s"}.${restart}` }
    : { kind: "danger", html: `${esc(body.detail || "Writing failed.")}${restart}` };
  cfgReset(id);
  cfgEdit.notice = notice;
  try {
    await loadOverview();
    if (devState.id === id) await loadDevicePage(id);
  } finally { devState.hold -= 1; }
}

function wireCfg() {
  const page = $("#devPage");
  const onValue = (ev) => {
    const el = ev.target.closest("[data-cfg]");
    if (!el) return;
    const f = cfgFields()[el.dataset.cfg];
    if (f) cfgSet(f.path, cfgRead(el, f));
  };
  page.addEventListener("input", onValue);
  page.addEventListener("change", onValue);
  page.addEventListener("click", (ev) => {
    const secLink = ev.target.closest("[data-cfg-sec]");   // select component
    if (secLink) {
      ev.preventDefault();
      cfgSel = { id: devState.id, sec: secLink.dataset.cfgSec };
      const y = window.scrollY;
      renderDeviceTab();
      window.scrollTo(0, y);
      const main = $(".rr-cfg-main");
      if (main && main.getBoundingClientRect().top < 0) main.scrollIntoView({ block: "start" });
      return;
    }
    const edit = ev.target.closest("[data-cfg-edit]");
    if (edit) {
      const comp = edit.dataset.cfgEdit;
      cfgEdit.id = devState.id;
      if (cfgEdit.open.has(comp)) cfgEdit.open.delete(comp); else cfgEdit.open.add(comp);
      const y = window.scrollY;   // keep the position, redrawing replaces the whole page
      renderDeviceTab();
      window.scrollTo(0, y);
      if (cfgEdit.open.has(comp)) $(`[data-cfg-card="${CSS.escape(comp)}"] [data-cfg]`)?.focus({ preventScroll: true });
      return;
    }
    const use = ev.target.closest("[data-cfg-use]");
    if (use) {
      const path = use.dataset.cfgUse, value = JSON.parse(use.dataset.value);
      cfgSet(path, value);
      const el = $(`[data-cfg="${CSS.escape(path)}"]`);
      if (el) {
        if (el.type === "checkbox") el.checked = !!value;
        else el.value = el.tagName === "SELECT" ? JSON.stringify(value) : value ?? "";
      }
      return;
    }
    if (ev.target.closest("[data-cfg-discard]")) { cfgReset(devState.id); renderDeviceTab(); return; }
    if (ev.target.closest("[data-cfg-review]")) { cfgReview().catch((e) => { cfgEdit.notice = { kind: "danger", html: esc(e.message) }; renderCfgBar(); }); return; }
    if (ev.target.closest("[data-cfg-back]")) { cfgEdit.review = null; renderCfgBar(); return; }
    if (ev.target.closest("[data-cfg-write]")) cfgWrite();
  });
}

async function toggleDiff(snapId) {
  const row = $(`#diff-${snapId}`);
  if (!row.classList.contains("d-none")) { show(row, false); return; }
  show(row, true);
  const cell = $("td", row);
  cell.innerHTML = `<span class="text-secondary">Loading …</span>`;
  try {
    const { body } = await api(`api/snapshots/${snapId}/diff`);
    cell.innerHTML = body.changes.length ? `<table class="table table-sm mb-0"><thead><tr><th>Path</th><th>Before</th><th>After</th></tr></thead><tbody>
      ${body.changes.map((c) => `<tr><td class="font-monospace small">${esc(c.path)}</td>
        <td class="rr-old">${c.kind === "added" ? `<span class="text-secondary">–</span>` : diffVal(c.old)}</td>
        <td class="rr-new">${c.kind === "removed" ? `<span class="text-secondary">removed</span>` : diffVal(c.new)}</td></tr>`).join("")}
      </tbody></table>` : `<span class="text-secondary">No differences.</span>`;
  } catch (e) { cell.innerHTML = `<span class="text-red">${esc(e.message)}</span>`; }
}

async function removeDevice() {
  const d = devState.body?.device;
  if (!d) return;
  if (!await confirmDialog(`Remove "${d.name || d.id}" and all its backups from RelayRoster? The device itself is not changed. If the next scan finds it again, it shows up as new.`, "Remove device", true)) return;
  try {
    await api(`api/devices/${encodeURIComponent(d.id)}`, { method: "DELETE" });
    devState = { id: null, tab: "overview", body: null, missing: false, hold: 0 };
    location.hash = "#devices";
    await loadOverview();
  } catch (e) { setDevMsg("error", e.message); }
}
