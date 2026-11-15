"use strict";
// Part of the UI, loads after app.js; shared global scope, order defined in index.html
// ---- Activity (the audit log) across all devices --------------------------------------------------------

const AUDIT_WHAT = { rename: "Name", firmware: "Firmware", reboot: "Restart", config: "Setting", auth: "Password",
  settings: "Settings", policy: "Policy" };
let auditFilter = { action: "", device_id: "" };

async function showAudit() {
  const q = new URLSearchParams(Object.entries(auditFilter).filter(([, v]) => v));
  const { body } = await api(`api/audit?${q}`);
  const devs = [...data.devices].sort(byName);
  const sel = (id, opts, cur, all) => `<select class="form-select form-select-sm rr-select" id="${id}">
    <option value="">${all}</option>${opts.map(([v, t]) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(t)}</option>`).join("")}</select>`;
  const val = (a, v) => { if (a.action !== "config") return esc(v || ""); try { return esc(fmtVal(JSON.parse(v))); } catch { return esc(v || ""); } };
  const rows = body.entries.map((a) => `<tr><td class="text-nowrap">${timeLongHtml(a.at)}</td>
      <td>${a.device_id ? devLink(a.device_id, a.device_name || a.device_id) : `<span class="text-secondary">All devices</span>`}</td>
      <td>${esc(AUDIT_WHAT[a.action] || a.action)}</td>
      <td>${a.old || a.new ? `${val(a, a.old) || "–"}<span class="rr-arrow">→</span>${val(a, a.new)}` : `<span class="text-secondary">–</span>`}</td>
      <td>${a.ok ? badge("ok", "ok") : badge("bad", "failed")}${a.detail ? `<span class="rr-sub">${esc(a.detail)}</span>` : ""}</td></tr>`).join("");
  $("#auditPage").innerHTML = `<div class="d-flex gap-2 flex-wrap align-items-center mb-3">
      ${sel("auFilterAction", body.actions.map((x) => [x, AUDIT_WHAT[x] || x]), auditFilter.action, "Any action")}
      ${sel("auFilterDevice", devs.map((d) => [d.id, d.name || d.id]), auditFilter.device_id, "Any device")}
      <span class="text-secondary small ms-auto">${body.entries.length} ${body.entries.length === 1 ? "entry" : "entries"}</span>
      <a class="btn btn-sm" href="api/audit.csv?${q}" download>${ICON.download}CSV</a></div>
    ${body.entries.length ? `<div class="card">${cardTable(["When", "Device", "What", "Change", "Result"], rows)}</div>`
      : `<div class="card"><div class="empty"><p class="empty-title">No entries</p></div></div>`}`;
  $("#auFilterAction").addEventListener("change", (ev) => { auditFilter.action = ev.target.value; showAudit().catch((e) => showError(e.message)); });
  $("#auFilterDevice").addEventListener("change", (ev) => { auditFilter.device_id = ev.target.value; showAudit().catch((e) => showError(e.message)); });
}
