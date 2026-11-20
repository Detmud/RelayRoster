"use strict";
// Part of the UI, loads after app.js; shared global scope, order defined in index.html
// ---- Address plan: give devices a static address ----------------------------------------------
// Preview from the server (suggested address per device, netmask/gateway/DNS the call would use), the address
// can be edited, then a job moves one device after the other and waits for each at its new address.

const ADDR_STATUS = {
  queued: ["neutral", "waiting"], checking: ["info", "checking"], moving: ["info", "setting address"],
  waiting: ["info", "waiting for device"], done: ["ok", "moved"], failed: ["bad", "failed"], skipped: ["neutral", "skipped"],
};
let allowAddress = false;
let addrWasRunning = false;
let addrTimer = null;

const addrCandidates = () => [...new Set(data.findings.filter((f) => f.kind === "network" && f.target).map((f) => f.device_id))];
const addrOffHint = () => (allowAddress ? "" : "Turn on “Allow address changes” in Settings.");

// Bar on the Findings page: devices that are not in their block
function renderAddrBar() {
  const ids = addrCandidates();
  const bar = $("#addrBar");
  show(bar, ids.length > 0);
  bar.innerHTML = ids.length ? `<div class="d-flex align-items-center gap-3 flex-wrap"><div class="flex-fill rr-minw0">
      <strong>${plural(ids.length, "device")} outside the address plan.</strong> RelayRoster can give them the next free address of their block as a static address.
      ${allowAddress ? "" : `<div class="small text-secondary">The preview works without it; to write, turn on “Allow address changes” in <a href="#settings">Settings</a>.</div>`}
    </div><button class="btn btn-sm" type="button" data-addr-open="">Apply address plan…</button></div>` : "";
}

function renderAddrJobBar(job) {
  const bar = $("#addrJobBar");
  if (!job?.running) { show(bar, false); return; }
  const cur = job.current;
  bar.innerHTML = `<div class="d-flex align-items-center gap-3 flex-wrap"><div class="flex-fill rr-minw0">
      <strong>Address change running</strong> · ${job.done} of ${job.total} done${job.cancelled ? " · stopping after current device" : ""}
      ${cur ? `<div class="small text-secondary">${esc(cur.name || cur.device_id)}: ${esc(cur.old)} → ${esc(cur.new)} · ${esc((ADDR_STATUS[cur.status] || [0, cur.status])[1])}</div>` : ""}
    </div><button class="btn btn-sm" type="button" id="addrJobShow">Show</button></div>`;
  show(bar, true);
  $("#addrJobShow").addEventListener("click", openAddrJob);
}

// deviceId: only this device, whatever its block (its current address keeps it in place, DHCP -> static);
// empty: every device outside its block
async function openAddrDialog(deviceId) {
  $("#addrBody").innerHTML = `<p class="text-secondary mb-0">Building the preview…</p>`;
  $("#addrSummary").textContent = "";
  show($("#addrStart"), false);
  show($("#addrStop"), false);
  $("#addrClose").textContent = "Cancel";
  if (!$("#addrDlg").open) $("#addrDlg").showModal();
  try {
    const { body } = await api("api/address/preview", { method: "POST", json: deviceId ? { device_ids: [deviceId] } : {} });
    if (body.errors?.length) throw new Error(body.errors.join(" · "));
    renderAddrPreview(body.devices, !!deviceId);
  } catch (e) {
    $("#addrBody").innerHTML = `<div class="alert alert-danger mb-0">${esc(e.message)}</div>`;
  }
}

function renderAddrPreview(rows, single = false) {
  if (!rows.length) { $("#addrBody").innerHTML = `<p class="text-secondary mb-0">Every device with an address block is inside its block.</p>`; return; }
  $("#addrBody").innerHTML = `
    ${allowAddress ? "" : `<div class="alert alert-warning">Writing is turned off. Turn on “Allow address changes” in <a href="#settings">Settings</a>; the preview works without it.</div>`}
    <div class="alert alert-warning"><strong>Everything that addresses these devices by IP needs the new address afterwards</strong>
      (home automation, scripts and webhooks of other devices, MQTT ACLs). The address is set on the device itself, not as a reservation in the router:
      keep the blocks outside the router's DHCP pool. There is no rollback; a device that does not come back has to be found at one of the two addresses.</div>
    <div class="card"><div class="table-responsive"><table class="table table-vcenter card-table">
      <thead><tr><th class="w-1"><input class="form-check-input" type="checkbox" id="addrAll" checked aria-label="Select all"></th>
        <th>Device</th><th data-type="ip">Current</th><th>New address</th><th>Network</th></tr></thead>
      <tbody>${rows.map((r) => `<tr data-addr="${esc(r.device_id)}">
        <td><input class="form-check-input" type="checkbox"${r.error ? " disabled" : " checked"} aria-label="Select ${esc(r.name || r.device_id)}"></td>
        <td>${esc(r.name || "(no name)")}<span class="rr-sub">${esc(r.model_name)}${r.block ? ` · block ${esc(r.block)}` : ""}</span></td>
        <td class="font-monospace small">${esc(r.host)}</td>
        <td><input class="form-control form-control-sm font-monospace rr-addr-input" value="${esc(r.target || "")}" inputmode="decimal" autocomplete="off"
          aria-label="New address of ${esc(r.name || r.device_id)}"${r.error ? " disabled" : ""}></td>
        <td>${r.error ? `<span class="text-red small">${esc(r.error)}</span>`
          : `<span class="small">${esc(r.iface === "eth" ? "Ethernet" : "Wi-Fi")} · mask ${esc(r.netmask)} · gateway ${esc(r.gw)} · DNS ${esc(r.nameserver || "none")}</span>
             <span class="rr-sub">${r.source === "device" ? "from the device's static configuration" : "from network in the policy (device uses DHCP)"}</span>`}</td></tr>`).join("")}</tbody></table></div></div>
    <p class="text-secondary small mt-3 mb-0">One device after another. RelayRoster checks that nothing answers at the new address, sets it, waits for the device there and compares its ID. Moving shutters are skipped.</p>`;
  const boxes = () => $$("tbody input[type=checkbox]", $("#addrBody"));
  const update = () => {
    const n = boxes().filter((b) => b.checked).length;
    $("#addrStart").textContent = `Move ${plural(n, "device")}`;
    $("#addrStart").disabled = n === 0 || !allowAddress;
    $("#addrStart").title = addrOffHint();
  };
  $("#addrAll").addEventListener("change", (ev) => { boxes().forEach((b) => { if (!b.disabled) b.checked = ev.target.checked; }); update(); });
  boxes().forEach((b) => b.addEventListener("change", update));
  show($("#addrStart"), true);
  $("#addrSummary").textContent = single ? "Keep the current address to switch from DHCP to static" : `${plural(rows.length, "device")} outside its block`;
  update();
}

async function startAddrJob() {
  const items = $$("tr[data-addr]", $("#addrBody")).filter((r) => $("input[type=checkbox]", r).checked)
    .map((r) => ({ device_id: r.dataset.addr, ip: $(".rr-addr-input", r).value.trim() }));
  if (!items.length) return;
  $("#addrStart").disabled = true;
  try {
    const { status, body } = await api("api/address/apply", { method: "POST", json: { items } });
    if (status === 422) throw new Error(body.detail);
    renderAddrJob(body.job);
    addrWasRunning = true;
    pollAddrJob();
    pollState();
  } catch (e) {
    $("#addrSummary").innerHTML = `<span class="text-red">${esc(e.message)}</span>`;
    $("#addrStart").disabled = false;
  }
}

function renderAddrJob(job) {
  if (!job) { $("#addrBody").innerHTML = `<p class="text-secondary mb-0">No address change has run yet.</p>`; return; }
  $("#addrBody").innerHTML = `<div class="card"><div class="table-responsive"><table class="table table-vcenter card-table">
    <thead><tr><th>Device</th><th data-type="ip">Before</th><th data-type="ip">After</th><th>Result</th></tr></thead>
    <tbody>${job.items.map((it) => {
      const [kind, text] = ADDR_STATUS[it.status] || ["neutral", it.status];
      return `<tr><td>${esc(it.name || it.device_id)}</td>
      <td class="font-monospace small">${esc(it.old)}</td>
      <td class="font-monospace small fw-bold">${esc(it.new)}</td>
      <td>${badge(kind, text)}${it.duration_s != null ? ` <span class="text-secondary small">${it.duration_s} s</span>` : ""}
        ${it.error ? `<span class="rr-sub text-red">${esc(it.error)}</span>` : ""}</td></tr>`;
    }).join("")}</tbody></table></div></div>`;
  const c = job.counts || {};
  const parts = [c.done && `${c.done} moved`, c.failed && `${c.failed} failed`, c.skipped && `${c.skipped} skipped`].filter(Boolean);
  $("#addrSummary").textContent = job.running
    ? `${job.done} of ${job.total} done${job.cancelled ? " · stopping after current device" : ""} · Closing this window does not stop the job.`
    : (parts.join(", ") || "Finished.");
  show($("#addrStart"), false);
  show($("#addrStop"), job.running && !job.cancelled);
  $("#addrClose").textContent = "Close";
}

async function pollAddrJob() {
  clearTimeout(addrTimer);
  if (!$("#addrDlg").open) return;
  try {
    const { body } = await api("api/address/job");
    renderAddrJob(body.job);
    if (body.job?.running) addrTimer = setTimeout(pollAddrJob, 1500);
  } catch (e) { $("#addrSummary").textContent = e.message; addrTimer = setTimeout(pollAddrJob, 3000); }
}

function openAddrJob() {
  if (!$("#addrDlg").open) $("#addrDlg").showModal();
  pollAddrJob();
}

async function stopAddrJob() {
  $("#addrStop").disabled = true;
  try { await api("api/address/job/cancel", { method: "POST" }); } catch (e) { showError(e.message); }
  $("#addrStop").disabled = false;
  pollAddrJob();
}
