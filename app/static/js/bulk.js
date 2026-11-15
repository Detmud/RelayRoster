"use strict";
// Part of the UI, loads after app.js; shared global scope, order defined in index.html
// ---- Apply profile, bulk change ------------------------------------------------------
// One dialog for all paths (apply profile, bulk change, "Fix on all"): preview per device from the server, rows
// can be deselected, the exact calls below. Writing runs as a job, one device after the other.

const APPLY_STATUS = {
  change: ["warn", "change"], ok: ["ok", "already set"], unsupported: ["neutral", "not supported"],
  na: ["neutral", "not present"], not_writable: ["neutral", "not writable"], hint: ["info", "no single value"],
  overridden: ["neutral", "override"], invalid: ["bad", "invalid"], nodata: ["neutral", "no data"],
};
const JOB_STATUS = { queued: ["neutral", "queued"], writing: ["info", "writing"], restarting: ["info", "restarting"],
  done: ["ok", "done"], failed: ["bad", "failed"], skipped: ["warn", "skipped"] };
// Same mapping as app/writable.py METHODS, so the preview is right after deselecting
// SetConfig of the component available according to Shelly.ListMethods? Unknown = yes
const offersSet = (secId) => {
  const methods = devState.body?.methods;
  const m = SET_METHODS[String(secId).split(":")[0]];
  return !methods || !m || methods.some((x) => x.toLowerCase() === m.toLowerCase());   // Wifi/WiFi, Mqtt/MQTT
};
const SET_METHODS = { sys: "Sys.SetConfig", wifi: "WiFi.SetConfig", cloud: "Cloud.SetConfig", mqtt: "MQTT.SetConfig",
  cover: "Cover.SetConfig", switch: "Switch.SetConfig", input: "Input.SetConfig", ble: "BLE.SetConfig",
  ws: "Ws.SetConfig", matter: "Matter.SetConfig", knx: "KNX.SetConfig",
  modbus: "Modbus.SetConfig" };
let applyCtx = null;     // { title, request, policy, plans }
let applyTimer = null;
let applyWasRunning = false;

function buildCalls(changes) {
  const byComp = new Map();
  for (const path of Object.keys(changes).sort()) {
    const comp = path.split(".")[0];
    const [kind, idx] = comp.split(":");
    if (!byComp.has(comp)) byComp.set(comp, { method: SET_METHODS[kind], params: idx !== undefined ? { id: +idx, config: {} } : { config: {} } });
    let node = byComp.get(comp).params.config;
    const parts = path.slice(comp.length + 1).split(".");
    for (const p of parts.slice(0, -1)) node = node[p] ??= {};
    node[parts.at(-1)] = changes[path];
  }
  return [...byComp.values()];
}

async function openApply(ctx) {
  applyCtx = { ...ctx, plans: null };
  $("#applyTitle").textContent = ctx.title;
  $("#applyBody").innerHTML = `<p class="text-secondary mb-0">Building the preview…</p>`;
  $("#applySummary").textContent = "";
  show($("#applyStart"), false);
  show($("#applyStop"), false);
  $("#applyClose").textContent = "Cancel";
  if (!$("#applyDlg").open) $("#applyDlg").showModal();
  try {
    const { status, body } = await api("api/apply/preview", { method: "POST", json: ctx.request, keep: "all" });
    if (status >= 400) throw new Error((body.errors || [body.detail]).join(" · "));
    applyCtx.plans = body.devices;
    renderApplyPreview();
  } catch (e) {
    $("#applyBody").innerHTML = `<div class="alert alert-danger mb-0">${esc(e.message)}</div>`;
  }
}

function applySelected() {
  const out = {};
  for (const el of $$("input[data-apply-row]", $("#applyBody"))) {
    if (!el.checked) continue;
    const [i, path] = [+el.dataset.dev, el.dataset.applyRow];
    const plan = applyCtx.plans[i];
    (out[plan.device_id] ??= {})[path] = plan.changes[path];
  }
  return out;
}

function renderApplyPreview() {
  const plans = applyCtx.plans;
  const writable = plans.filter((p) => Object.keys(p.changes || {}).length);
  const rows = plans.map((p, i) => {
    const head = `<tr class="rr-devrow"><td colspan="5">${devLink(p.device_id, p.name || p.device_id)}
      <span class="text-secondary small fw-normal">${esc(p.model_name || "")} · ${esc(p.host || "")}${p.note ? ` · ${esc(p.note)}` : ""}</span></td></tr>`;
    const body = (p.rows || []).map((r) => {
      const [kind, text] = APPLY_STATUS[r.status] || ["neutral", r.status];
      const can = r.status === "change" && Object.prototype.hasOwnProperty.call(p.changes || {}, r.path);
      return `<tr class="${can ? "" : "rr-skip"}">
        <td class="w-1">${can ? `<input class="form-check-input" type="checkbox" checked data-dev="${i}" data-apply-row="${esc(r.path)}" aria-label="Write ${esc(r.path)}">` : ""}</td>
        <td class="font-monospace small">${esc(r.path)}<span class="rr-sub">${esc(r.label || "")}</span></td>
        <td>${r.old === null && r.status !== "change" ? `<span class="text-secondary">–</span>` : valHtml(r.old)}</td>
        <td>${valHtml(r.new)}${r.unit ? ` <span class="text-secondary small">${esc(r.unit)}</span>` : ""}</td>
        <td>${badge(kind, text, r.note || "")}${r.note && r.status !== "ok" ? `<span class="rr-sub">${esc(r.note)}</span>` : ""}</td></tr>`;
    }).join("");
    return head + (body || `<tr class="rr-skip"><td></td><td colspan="4" class="text-secondary small">Nothing to write.</td></tr>`);
  }).join("");
  const pol = applyCtx.policy;
  $("#applyBody").innerHTML = `
    ${allowConfig ? "" : `<div class="alert alert-warning">Writing is turned off. Turn on “Allow config changes” in <a href="#settings">Settings</a>; the preview works without it.</div>`}
    <div class="card"><div class="table-responsive"><table class="table table-vcenter card-table rr-apply">
      <thead><tr><th class="w-1"><input class="form-check-input" type="checkbox" id="applyAll" checked aria-label="Select all"></th>
        <th>Setting</th><th>Current</th><th>New</th><th>Status</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="5" class="text-secondary">No devices.</td></tr>`}</tbody></table></div></div>
    ${pol ? `<div class="mt-3"><label class="form-check"><input class="form-check-input" type="checkbox" id="applyPolicy" checked>
        <span class="form-check-label">Also add the rule to <code>policy.yaml</code>: ${pol.scope === "baseline" ? "<b>baseline</b> (all devices)" : "<b>overrides</b> of each written device"}</span></label>
        <input class="form-control form-control-sm mt-1" id="applyComment" value="${esc(pol.comment)}" aria-label="Comment in policy.yaml">
        <div class="form-hint">With the rule in the policy, RelayRoster reports a device that drifts away again (e.g. after a factory reset). Untick for a one-time change.</div></div>` : ""}
    ${allowReboot ? `<label class="form-check mt-3"><input class="form-check-input" type="checkbox" id="applyRestart">
        <span class="form-check-label">Restart devices that report <code>restart_required</code></span></label>` : ""}
    <details class="mt-3"><summary class="text-secondary small">Exact calls per device</summary><div id="applyCalls" class="mt-2"></div></details>`;
  const boxes = () => $$("input[data-apply-row]", $("#applyBody"));
  const update = () => {
    const sel = applySelected();
    const n = Object.keys(sel).length, m = Object.values(sel).reduce((a, c) => a + Object.keys(c).length, 0);
    $("#applyStart").textContent = `Write ${plural(m, "value")} to ${plural(n, "device")}`;
    $("#applyStart").disabled = n === 0 || !allowConfig;
    $("#applyCalls").innerHTML = Object.entries(sel).map(([id, ch]) => {
      const p = plans.find((x) => x.device_id === id);
      return `<div class="mb-2"><b>${esc(p.name || id)}</b>${buildCalls(ch).map((c) => `<pre class="rr-apply">${esc(`${c.method} ${JSON.stringify(c.params)}`)}</pre>`).join("")}</div>`;
    }).join("") || `<span class="text-secondary small">Nothing selected.</span>`;
    const skip = plans.length - writable.length;
    $("#applySummary").textContent = `${plural(plans.length, "device")} checked${skip ? ` · ${skip} without changes` : ""}`;
  };
  $("#applyAll").addEventListener("change", (ev) => { boxes().forEach((b) => { b.checked = ev.target.checked; }); update(); });
  boxes().forEach((b) => b.addEventListener("change", update));
  show($("#applyStart"), true);
  update();
}

async function startApply() {
  const sel = applySelected();
  const items = Object.entries(sel).map(([device_id, changes]) => ({ device_id, changes }));
  if (!items.length) return;
  const json = { items, title: applyCtx.title, restart: !!$("#applyRestart")?.checked };
  if (applyCtx.policy && $("#applyPolicy")?.checked) {
    json.policy = { ...applyCtx.policy, comment: $("#applyComment").value.trim() };
  }
  $("#applyStart").disabled = true;
  try {
    const { status, body } = await api("api/apply", { method: "POST", json, keep: "all" });
    if (status >= 400) throw new Error((body.errors || [body.detail]).join(" · "));
    if (json.policy) { policyLoaded = false; prof.loaded = false; }
    applyWasRunning = true;
    renderApplyJob(body.job);
    pollApplyJob();
    pollState();
  } catch (e) {
    $("#applySummary").innerHTML = `<span class="text-red">${esc(e.message)}</span>`;
    $("#applyStart").disabled = false;
  }
}

function renderApplyJob(job) {
  if (!job) { $("#applyBody").innerHTML = `<p class="text-secondary mb-0">Nothing has been written yet.</p>`; return; }
  $("#applyTitle").textContent = job.title;
  $("#applyBody").innerHTML = `<div class="card"><div class="table-responsive"><table class="table table-vcenter card-table rr-apply">
    <thead><tr><th>Device</th><th>Values</th><th>Result</th></tr></thead>
    <tbody>${job.items.map((it) => {
      const [kind, text] = JOB_STATUS[it.status] || ["neutral", it.status];
      return `<tr><td>${devLink(it.device_id, it.name || it.device_id)}<span class="rr-sub">${esc(it.host)}</span></td>
        <td class="small">${Object.entries(it.changes).map(([p, v]) => `<div><span class="font-monospace">${esc(p)}</span> → ${valHtml(v)}</div>`).join("")}</td>
        <td>${badge(kind, text)}${it.duration_s != null ? ` <span class="text-secondary small">${it.duration_s} s</span>` : ""}
          ${it.restart_required || it.restarted ? ` ${badge(it.restarted ? "ok" : "warn", it.restarted ? "restarted" : "restart required")}` : ""}
          ${it.error ? `<span class="rr-sub text-red">${esc(it.error)}</span>` : ""}</td></tr>`;
    }).join("")}</tbody></table></div></div>`;
  const c = job.counts || {};
  const parts = [c.done && `${c.done} ${job.restart_only ? "restarted" : "written"}`, c.failed && `${c.failed} failed`, c.skipped && `${c.skipped} skipped`].filter(Boolean);
  $("#applySummary").textContent = (job.running ? `${job.done} of ${job.total} done${job.cancelled ? " · stopping after current device" : ""}` : (parts.join(", ") || "Finished.")) + (job.running ? " · Closing this window does not stop the job." : "");
  show($("#applyStart"), false);
  show($("#applyStop"), job.running && !job.cancelled);
  $("#applyClose").textContent = "Close";
}

async function pollApplyJob() {
  clearTimeout(applyTimer);
  if (!$("#applyDlg").open) return;
  try {
    const { body } = await api("api/apply/job");
    renderApplyJob(body.job);
    if (body.job?.running) applyTimer = setTimeout(pollApplyJob, 1500);
  } catch (e) { $("#applySummary").textContent = e.message; applyTimer = setTimeout(pollApplyJob, 3000); }
}

function renderApplyJobBar(job) {
  const bar = $("#applyJobBar");
  if (!job?.running) { show(bar, false); return; }
  const cur = job.current;
  bar.innerHTML = `<div class="d-flex align-items-center gap-3 flex-wrap"><div class="flex-fill rr-minw0">
      <strong>${esc(job.title)}</strong> · ${job.done} of ${job.total} done${job.cancelled ? " · stopping after current device" : ""}
      ${cur ? `<div class="small text-secondary">${esc(cur.name || cur.device_id)}: ${esc((JOB_STATUS[cur.status] || [0, cur.status])[1])}</div>` : ""}
    </div><button class="btn btn-sm" type="button" id="applyJobShow">Show</button></div>`;
  show(bar, true);
  $("#applyJobShow").addEventListener("click", () => { if (!$("#applyDlg").open) $("#applyDlg").showModal(); pollApplyJob(); });
}

async function stopApply() {
  $("#applyStop").disabled = true;
  try { await api("api/apply/job/cancel", { method: "POST" }); } catch (e) { showError(e.message); }
  $("#applyStop").disabled = false;
  pollApplyJob();
}

const applyProfile = (name, ids) => openApply({ title: `Apply profile ${name}`, policy: null,
  request: { mode: "profile", profile: name, ...(ids ? { device_ids: ids } : {}) } });
const fixAll = (path, value, ids) => openApply({ title: `Set ${path} on ${plural(ids.length, "device")}`, policy: null,
  request: { mode: "bulk", path, value, device_ids: ids } });

// ---- Bulk change page ----
let bulk = { loaded: false, paths: [], devices: [], path: "", value: "", target: "all", type: "", picked: new Set(), preset: null };
const bulkMeta = () => bulk.paths.find((p) => p.path === bulk.path);
const today = () => new Date().toISOString().slice(0, 10);

async function showBulk() {
  if (!bulk.loaded || bulk.preset) {
    const { body } = await api("api/bulk/paths");
    Object.assign(bulk, { loaded: true, paths: body.paths, devices: body.devices });
  }
  if (bulk.preset) { bulk.target = "pick"; bulk.picked = new Set(bulk.preset); bulk.preset = null; }
  renderBulk();
}

function bulkValueInput(m) {
  const v = bulk.value;
  if (!m) return `<input class="form-control" id="bkValue" value="${esc(v)}" autocomplete="off">`;
  if (m.kind === "bool") return `<select class="form-select" id="bkValue"><option value="">Choose…</option>
      <option value="true"${v === "true" ? " selected" : ""}>on (true)</option><option value="false"${v === "false" ? " selected" : ""}>off (false)</option></select>`;
  if (m.kind === "enum") return `<select class="form-select" id="bkValue"><option value="">Choose…</option>${(m.options || []).map((o) =>
      `<option value="${esc(JSON.stringify(o))}"${v === JSON.stringify(o) ? " selected" : ""}>${esc(o === null ? "none" : o)}</option>`).join("")}</select>`;
  if (m.kind === "number") return `<div class="input-group"><input class="form-control" type="number" step="any" id="bkValue" value="${esc(v)}"${m.min != null ? ` min="${m.min}"` : ""}${m.max != null ? ` max="${m.max}"` : ""}>
      ${m.unit ? `<span class="input-group-text">${esc(m.unit)}</span>` : ""}</div>`;
  return `<input class="form-control" id="bkValue" value="${esc(v)}" autocomplete="off">`;
}

function bulkParsedValue() {
  const m = bulkMeta(), t = bulk.value;
  if (t === "") throw new Error("Enter a value");
  if (m?.kind === "bool") return t === "true";
  if (m?.kind === "enum") return JSON.parse(t);
  if (m?.kind === "number") { const n = Number(t); if (!Number.isFinite(n)) throw new Error("Value must be a number"); return n; }
  return t;
}

function bulkTargets() {
  const m = bulkMeta();
  const has = new Set(m ? m.devices : bulk.devices.map((d) => d.id));
  if (bulk.target === "type") return bulk.devices.filter((d) => (d.model_name || d.app) === bulk.type && has.has(d.id)).map((d) => d.id);
  if (bulk.target === "pick") return [...bulk.picked];
  return bulk.devices.filter((d) => has.has(d.id)).map((d) => d.id);
}

// Job across several devices one after the other: restart or device password, progress in the bulk dialog
const restartMany = (ids) => deviceJob("api/restart", ids, "Restart devices",
  `Restart ${plural(ids.length, "device")} one after another? Each device is offline for a few seconds; moving shutters are skipped.`);
const passwordMany = (ids) => deviceJob("api/auth/rollout", ids, "Set password",
  `Set the device password from Settings on ${plural(ids.length, "device")}? Devices that already have a password are skipped. Anything else that talks to these devices then needs this password too.`);

async function deviceJob(url, ids, action, question) {
  if (!ids.length) { $("#bkMsg").innerHTML = `<span class="text-red">No devices selected</span>`; return; }
  if (!await confirmDialog(question, action)) return;
  try {
    const { status, body } = await api(url, { method: "POST", json: { device_ids: ids }, keep: "all" });
    if (status >= 400) throw new Error((body.errors || [body.detail]).join(" · "));
    applyWasRunning = true;
    if (!$("#applyDlg").open) $("#applyDlg").showModal();
    renderApplyJob(body.job);
    pollApplyJob();
    pollState();
  } catch (e) { $("#bkMsg").innerHTML = `<span class="text-red">${esc(e.message)}</span>`; }
}

function renderBulk() {
  const m = bulkMeta();
  const types = [...new Set(bulk.devices.map((d) => d.model_name || d.app))].sort();
  const has = new Set(m ? m.devices : []);
  const ids = bulkTargets();
  $("#bulkPage").innerHTML = `<div class="row row-cards">
    <div class="col-lg-6"><div class="card"><div class="card-header"><h3 class="card-title">Setting</h3></div><div class="card-body">
      <div class="mb-3"><label class="form-label required" for="bkPath">Setting</label>
        <input class="form-control font-monospace" id="bkPath" list="bkPaths" value="${esc(bulk.path)}" placeholder="sys.sntp.server" autocomplete="off">
        <datalist id="bkPaths">${bulk.paths.map((p) => `<option value="${esc(p.path)}">${esc(p.label)} · ${p.devices.length} devices</option>`).join("")}</datalist>
        <div class="form-hint">Only settings RelayRoster may write. <code>cover:*</code> means every cover of a device.</div></div>
      ${m ? `<div class="mb-3"><div class="form-label">Current values</div>${m.values.slice(0, 8).map((v) =>
          `<span class="badge bg-secondary-lt me-1 mb-1">${esc(fmtVal(v.value))} × ${v.count}</span>`).join("")}
          ${m.values.length > 8 ? `<span class="text-secondary small">+${m.values.length - 8} more</span>` : ""}</div>`
        : bulk.path ? `<div class="alert alert-warning">No device reports <code>${esc(bulk.path)}</code> as a writable setting.</div>` : ""}
      <div class="mb-3"><label class="form-label required" for="bkValue">New value</label>${bulkValueInput(m)}</div>
    </div></div></div>
    <div class="col-lg-6"><div class="card"><div class="card-header"><h3 class="card-title">Devices</h3>
      <div class="card-actions text-secondary small">${plural(ids.length, "device")}</div></div><div class="card-body">
      <label class="form-check"><input class="form-check-input" type="radio" name="bkTarget" value="all"${bulk.target === "all" ? " checked" : ""}>
        <span class="form-check-label">All devices that have this setting${m ? ` (${m.devices.length})` : ""}</span></label>
      <label class="form-check"><input class="form-check-input" type="radio" name="bkTarget" value="type"${bulk.target === "type" ? " checked" : ""}>
        <span class="form-check-label">One type</span></label>
      ${bulk.target === "type" ? `<select class="form-select form-select-sm mb-2" id="bkType"><option value="">Choose…</option>${types.map((t) => `<option${t === bulk.type ? " selected" : ""}>${esc(t)}</option>`).join("")}</select>` : ""}
      <label class="form-check"><input class="form-check-input" type="radio" name="bkTarget" value="pick"${bulk.target === "pick" ? " checked" : ""}>
        <span class="form-check-label">Choose devices</span></label>
      ${bulk.target === "pick" ? `<div class="rr-pick border rounded p-2" style="max-height:18rem;overflow:auto">${bulk.devices.map((d) => `<label class="form-check mb-1">
          <input class="form-check-input" type="checkbox" data-bk-dev="${esc(d.id)}"${bulk.picked.has(d.id) ? " checked" : ""}>
          <span class="form-check-label">${esc(d.name || d.id)} <span class="text-secondary small">${esc(d.model_name || d.app)}${m && !has.has(d.id) ? " · does not have this setting" : ""}</span></span></label>`).join("")}</div>` : ""}
      <div class="form-hint mt-2">Devices without the setting are listed in the preview and left alone.</div>
    </div></div></div>
    </div>
    <div class="d-flex gap-2 justify-content-end mt-3 align-items-center">
      <span class="text-secondary small me-auto" id="bkMsg"></span>
      ${allowSetPassword ? `<button class="btn" type="button" id="bkPassword" title="Set the device password from Settings on the selected devices that have none">Set password…</button>` : ""}
      ${allowReboot ? `<button class="btn" type="button" id="bkRestart" title="Restart the selected devices one after another, e.g. after changing the NTP server">Restart ${plural(ids.length, "device")}…</button>` : ""}
      <button class="btn btn-primary" type="button" id="bkPreview">Preview</button>
    </div>`;
  $("#bkPath").addEventListener("change", (ev) => { bulk.path = ev.target.value.trim(); bulk.value = ""; renderBulk(); });
  const val = $("#bkValue");
  val.addEventListener(val.tagName === "SELECT" ? "change" : "input", (ev) => { bulk.value = ev.target.value; });
  $$("input[name=bkTarget]").forEach((r) => r.addEventListener("change", () => { bulk.target = r.value; renderBulk(); }));
  $("#bkType")?.addEventListener("change", (ev) => { bulk.type = ev.target.value; renderBulk(); });
  $$("[data-bk-dev]").forEach((c) => c.addEventListener("change", () => {
    if (c.checked) bulk.picked.add(c.dataset.bkDev); else bulk.picked.delete(c.dataset.bkDev);
    $(".card-actions", $("#bulkPage .col-lg-6:last-child")).textContent = plural(bulkTargets().length, "device");
    if ($("#bkRestart")) $("#bkRestart").textContent = `Restart ${plural(bulkTargets().length, "device")}…`;
  }));
  $("#bkRestart")?.addEventListener("click", () => restartMany(bulkTargets()));
  $("#bkPassword")?.addEventListener("click", () => passwordMany(bulkTargets()));
  $("#bkPreview").addEventListener("click", () => {
    let value;
    try {
      if (!bulk.path) throw new Error("Choose a setting");
      value = bulkParsedValue();
    } catch (e) { $("#bkMsg").innerHTML = `<span class="text-red">${esc(e.message)}</span>`; return; }
    const targets = bulkTargets();
    if (!targets.length) { $("#bkMsg").innerHTML = `<span class="text-red">No devices selected</span>`; return; }
    const everyone = bulk.target === "all";
    openApply({ title: `Set ${bulk.path}`, request: { mode: "bulk", path: bulk.path, value, device_ids: targets },
      policy: { scope: everyone ? "baseline" : "overrides", path: bulk.path, value, comment: `Bulk change ${today()}` } });
  });
}
