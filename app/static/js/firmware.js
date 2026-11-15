"use strict";
// Part of the UI, loads after app.js; shared global scope, order defined in index.html
// ---- Firmware-Update -------------------------------------------------------------------

const FW_STATUS = {
  queued: ["neutral", "waiting"], checking: ["info", "checking"], updating: ["info", "updating"],
  rebooting: ["info", "restarting"], done: ["ok", "updated"], up_to_date: ["ok", "up to date"],
  failed: ["bad", "failed"], skipped: ["neutral", "skipped"],
};
const fwStatusBadge = (it) => {
  const [kind, text] = FW_STATUS[it.status] || ["neutral", it.status];
  return badge(kind, text, it.error || "");
};
let fwWasRunning = false;
let fwTimer = null;
let fwSelection = [];

function renderFwJobBar(job) {
  const bar = $("#fwJobBar");
  if (!job?.running) { show(bar, false); return; }
  const cur = job.current;
  bar.innerHTML = `<div class="d-flex align-items-center gap-3 flex-wrap"><div class="flex-fill sf-minw0">
      <strong>Firmware update running</strong> · ${job.done} of ${job.total} done${job.cancelled ? " · stopping after current device" : ""}
      ${cur ? `<div class="small text-secondary">${esc(cur.name || cur.device_id)}: ${esc((FW_STATUS[cur.status] || [0, cur.status])[1])}${cur.target ? ` → ${esc(cur.target)}` : ""}</div>` : ""}
    </div><button class="btn btn-sm" type="button" id="fwJobShow">Show</button></div>`;
  show(bar, true);
  $("#fwJobShow").addEventListener("click", () => openFwJob());
}

function openFwDialog(scope) {
  const all = data.devices.filter((d) => d.updates?.stable || d.updates?.beta);
  fwSelection = scope === "all" ? all.filter((d) => d.updates?.stable)
    : scope.startsWith("app:") ? all.filter((d) => d.app === scope.slice(4) && d.updates?.stable)
    : all.filter((d) => d.id === scope.slice(4));
  renderFwSelect("stable");
  $("#fwDlg").showModal();
}

function renderFwSelect(stage) {
  const rows = fwSelection.filter((d) => d.updates?.[stage]);
  const beta = fwSelection.some((d) => d.updates?.beta);
  $("#fwBody").innerHTML = `
    ${beta ? `<div class="mb-3"><label class="form-check form-switch"><input class="form-check-input" type="checkbox" id="fwBeta"${stage === "beta" ? " checked" : ""}>
      <span class="form-check-label">Use beta channel</span></label></div>` : ""}
    ${rows.length ? `<div class="card"><div class="table-responsive"><table class="table table-vcenter card-table">
      <thead><tr><th class="w-1"><input class="form-check-input" type="checkbox" id="fwAll" checked aria-label="Select all"></th>
        <th>Device</th><th>Installed</th><th>New</th><th>Result</th></tr></thead>
      <tbody>${rows.map((d) => `<tr data-fw="${esc(d.id)}">
        <td><input class="form-check-input" type="checkbox" checked aria-label="Select ${esc(d.name || d.id)}"></td>
        <td>${esc(d.name || "(no name)")}<span class="sf-sub">${esc(d.model_name || d.app)} · ${esc(d.host)}</span></td>
        <td class="font-monospace small">${esc(d.ver || "–")}</td>
        <td class="font-monospace small fw-bold">${esc(d.updates[stage])}</td>
        <td class="sf-result">${badge("neutral", "–")}</td></tr>`).join("")}</tbody></table></div></div>`
      : `<p class="text-secondary mb-0">No ${stage} update available for the selected devices.</p>`}
    <p class="text-secondary small mt-3 mb-0">RelayRoster checks each device for the latest version right before updating, waits for the restart and confirms the new version. Lights and shutters on a device are briefly unavailable during its restart.</p>`;
  $("#fwBeta")?.addEventListener("change", (ev) => renderFwSelect(ev.target.checked ? "beta" : "stable"));
  const boxes = () => $$("tbody input[type=checkbox]", $("#fwBody"));
  const update = () => {
    const n = boxes().filter((b) => b.checked).length;
    $("#fwStart").textContent = `Update ${n} device${n === 1 ? "" : "s"}`;
    $("#fwStart").disabled = n === 0;
  };
  $("#fwAll")?.addEventListener("change", (ev) => { boxes().forEach((b) => { b.checked = ev.target.checked; }); update(); });
  boxes().forEach((b) => b.addEventListener("change", update));
  $("#fwStart").dataset.stage = stage;
  show($("#fwStart"), rows.length > 0);
  show($("#fwCancel"), false);
  $("#fwClose").textContent = "Cancel";
  $("#fwSummary").textContent = "";
  update();
}

async function startFwUpdate() {
  const ids = $$("tr[data-fw]", $("#fwBody")).filter((r) => $("input", r).checked).map((r) => r.dataset.fw);
  if (!ids.length) return;
  $("#fwStart").disabled = true;
  try {
    const { status, body } = await api("api/firmware/update", { method: "POST", json: { device_ids: ids, stage: $("#fwStart").dataset.stage } });
    if (status === 422) throw new Error(body.detail);
    renderFwJob(body.job);
    fwWasRunning = true;
    pollFwJob();
    pollState();
  } catch (e) {
    $("#fwSummary").innerHTML = `<span class="text-red">${esc(e.message)}</span>`;
    $("#fwStart").disabled = false;
  }
}

function renderFwJob(job) {
  if (!job) { $("#fwBody").innerHTML = `<p class="text-secondary mb-0">No firmware update has run yet.</p>`; return; }
  $("#fwBody").innerHTML = `<div class="card"><div class="table-responsive"><table class="table table-vcenter card-table">
    <thead><tr><th>Device</th><th>Before</th><th>After</th><th>Result</th></tr></thead>
    <tbody>${job.items.map((it) => `<tr>
      <td>${esc(it.name || it.device_id)}<span class="sf-sub">${esc(it.host)}</span></td>
      <td class="font-monospace small">${esc(it.old || "–")}</td>
      <td class="font-monospace small fw-bold">${esc(it.new || it.target || "–")}</td>
      <td>${fwStatusBadge(it)}${it.duration_s != null ? ` <span class="text-secondary small">${it.duration_s} s</span>` : ""}
        ${it.error ? `<span class="sf-sub text-red">${esc(it.error)}</span>` : ""}</td></tr>`).join("")}</tbody></table></div></div>`;
  const c = job.counts || {};
  const parts = [c.done && `${c.done} updated`, c.up_to_date && `${c.up_to_date} already up to date`,
    c.failed && `${c.failed} failed`, c.skipped && `${c.skipped} skipped`].filter(Boolean);
  $("#fwSummary").textContent = job.running ? `${job.done} of ${job.total} done${job.cancelled ? " · stopping after current device" : ""}` : (parts.join(", ") || "Finished.");
  show($("#fwStart"), false);
  show($("#fwCancel"), job.running && !job.cancelled);
  $("#fwClose").textContent = "Close";
}

async function pollFwJob() {
  clearTimeout(fwTimer);
  if (!$("#fwDlg").open) return;
  try {
    const { body } = await api("api/firmware/job");
    renderFwJob(body.job);
    if (body.job?.running) fwTimer = setTimeout(pollFwJob, 1500);
  } catch (e) { $("#fwSummary").textContent = e.message; fwTimer = setTimeout(pollFwJob, 3000); }
}

function openFwJob() {
  if (!$("#fwDlg").open) $("#fwDlg").showModal();
  pollFwJob();
}

async function cancelFwJob() {
  $("#fwCancel").disabled = true;
  try { await api("api/firmware/job/cancel", { method: "POST" }); } catch (e) { showError(e.message); }
  $("#fwCancel").disabled = false;
  pollFwJob();
}

function renderBackups() {
  $("#bkTable tbody").innerHTML = data.devices.map((d) => `<tr class="cursor-pointer" data-id="${esc(d.id)}" data-section="backups">
    <td><div class="fw-bold">${devLink(d.id, d.name || "(no name)")}</div><span class="sf-sub" title="${esc(d.id)}">${esc(d.mac || d.id)}</span></td>
    <td>${typeLabel(d)}</td>
    <td class="text-end">${d.snapshot_count}</td>
    <td>${esc(fmtTime(d.last_change))}</td>
    <td class="text-secondary">${esc(ago(d.last_confirmed))}</td>
    <td>${d.last_snapshot ? `<a class="btn btn-sm" href="api/snapshots/${d.last_snapshot}/download" data-stop>${ICON.download}JSON</a>` : ""}</td>
  </tr>`).join("");
}
