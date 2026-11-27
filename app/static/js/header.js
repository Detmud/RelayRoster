"use strict";
// Part of the UI, loads after app.js; shared global scope, order defined in index.html
// ---- Rename ------------------------------------------------------------------------

let modeSet = false;

function setMode(rename, firmware = false) {
  if (modeSet && rename === allowRename && firmware === allowFirmware) return;
  const fwChanged = firmware !== allowFirmware;
  modeSet = true;
  allowRename = rename;
  allowFirmware = firmware;
  renderPlanBar();
  if (fwChanged) renderFirmware();
}

// ---- Kopfzeile: Split-Button -----------------------------------------------------------

function setMenu(menu, open) {
  menu.classList.toggle("show", open);
  menu.previousElementSibling?.setAttribute("aria-expanded", String(open));
  if (open) $(".dropdown-item:not(:disabled)", menu)?.focus();
}

function setSyncMenu(open) {
  const btn = $("#syncMenuBtn");
  $("#syncMenu").classList.toggle("show", open);
  btn.setAttribute("aria-expanded", String(open));
  if (open) $("#syncMenu .dropdown-item:not(:disabled)")?.focus();
}

let planChanges = [];

async function openPlan() {
  const dlg = $("#planDlg");
  $("#planBody").innerHTML = `<p class="text-secondary">Loading …</p>`;
  $("#planSummary").textContent = "";
  $("#planApply").disabled = true;
  show($("#planApply"), true);
  $("#planCancel").textContent = "Cancel";
  if (!dlg.open) dlg.showModal();
  try { ({ body: { changes: planChanges } } = await api("api/names/plan")); } catch (e) {
    $("#planBody").innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`;
    return;
  }
  if (!planChanges.length) {
    $("#planBody").innerHTML = `<p class="text-secondary mb-0">All devices already carry their planned names.</p>`;
    show($("#planApply"), false);
    return;
  }
  $("#planBody").innerHTML = `<div class="card"><div class="table-responsive"><table class="table table-vcenter card-table">
    <thead><tr><th class="w-1"><input class="form-check-input" type="checkbox" id="planAll" checked aria-label="Select all"></th>
      <th>Device</th><th>Current name</th><th>New name</th><th>Result</th></tr></thead>
    <tbody>${planChanges.map((c) => `<tr data-plan="${esc(c.device_id)}">
      <td><input class="form-check-input" type="checkbox" checked aria-label="Select ${esc(c.planned)}"></td>
      <td title="${esc(c.device_id)}">${esc(c.model_name || c.app || c.device_id)}<span class="rr-sub">${esc(c.host)}</span></td>
      <td>${c.current ? esc(c.current) : `<span class="text-secondary">(no name)</span>`}</td>
      <td class="fw-bold">${esc(c.planned)}</td>
      <td class="rr-result text-secondary">–</td></tr>`).join("")}</tbody></table></div></div>
    <p class="text-secondary small mt-3 mb-0">Home Assistant keeps its own device names and entity IDs. Every write is logged in the device's history.</p>`;
  const boxes = () => $$("tbody input[type=checkbox]", $("#planBody"));
  const update = () => {
    const n = boxes().filter((b) => b.checked).length;
    $("#planApply").textContent = `Write ${n} name${n === 1 ? "" : "s"}`;
    $("#planApply").disabled = n === 0;
  };
  $("#planAll").addEventListener("change", (ev) => { boxes().forEach((b) => { b.checked = ev.target.checked; }); update(); });
  boxes().forEach((b) => b.addEventListener("change", update));
  update();
}

async function applyPlan() {
  const rows = $$("tr[data-plan]", $("#planBody")).filter((r) => $("input", r).checked);
  const ids = rows.map((r) => r.dataset.plan);
  if (!ids.length) return;
  $("#planApply").disabled = true;
  $$("input", $("#planBody")).forEach((b) => { b.disabled = true; });
  rows.forEach((r) => { $(".rr-result", r).innerHTML = `<span class="text-secondary">writing …</span>`; });
  try {
    const { body } = await api("api/names/apply", { method: "POST", json: { device_ids: ids } });
    for (const res of body.results) {
      const row = $(`tr[data-plan="${CSS.escape(res.device_id)}"]`, $("#planBody"));
      if (row) $(".rr-result", row).innerHTML = res.ok ? badge("ok", "written") : badge("bad", "failed", res.error);
      if (row && !res.ok) $(".rr-result", row).insertAdjacentHTML("beforeend", `<span class="rr-sub text-red">${esc(res.error)}</span>`);
    }
    $("#planSummary").textContent = `${body.ok} written${body.failed ? `, ${body.failed} failed` : ""}.`;
  } catch (e) {
    $("#planSummary").textContent = e.message;
  }
  show($("#planApply"), false);
  $("#planCancel").textContent = "Close";
  await loadOverview();
}
