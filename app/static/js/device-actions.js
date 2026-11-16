"use strict";
// Part of the UI, loads after app.js; shared global scope, order defined in index.html
// ---- Edit the name on the device page -------------------------------------------------
// The name is a locked field in the page header and only becomes editable with the pencil. "Write to device" and
// "Cancel" are below it and only visible while editing. Enter writes, Esc cancels.

let nameEdit = null;   // { id, current } while editing

// The name is a normal setting (Settings tab → System); the header only shows it
function nameField(d, planned) {
  const current = d.name || "";
  const plan = planned && planned !== current
    ? `<div class="small text-secondary mt-1 fw-normal" id="namePlan">Plan: ${esc(planned)}</div>` : "";
  return `<div class="rr-name rr-name-title">${current ? esc(current) : `<span class="text-secondary">(no name)</span>`}${plan}</div>`;
}

function setNameMsg(kind, text) {
  const msg = $("#nameMsg");
  if (!msg) return;
  msg.className = `small mt-1 fw-normal${kind === "error" ? " text-red" : kind === "ok" ? " text-green" : " text-secondary"}`;
  msg.textContent = text || "";
  $("#nameInput")?.classList.toggle("is-invalid", kind === "error");
}

function startNameEdit(value) {
  const input = $("#nameInput");
  if (!input || !allowRename) return;
  nameEdit = { id: input.dataset.device, current: input.dataset.current };
  input.readOnly = false;
  input.closest(".rr-name").classList.add("rr-editing");
  if (value !== undefined) input.value = value;
  setNameMsg("hint", "");
  show($("#nameActions"), true);
  $("#nameSave").disabled = false;
  $("#nameCancel").disabled = false;
  $("#nameSave").textContent = "Write to device";
  input.focus();
  input.select();
}

function endNameEdit(reset = true) {
  const actions = $("#nameActions");
  if (actions) show(actions, false);
  const input = $("#nameInput");
  if (input) {
    input.readOnly = true;
    input.closest(".rr-name").classList.remove("rr-editing");
    if (reset && nameEdit) input.value = nameEdit.current;
    input.classList.remove("is-invalid");
  }
  if (reset) setNameMsg("hint", "");
  nameEdit = null;
}

async function saveName() {
  if (!nameEdit || nameEdit.writing) return;
  const input = $("#nameInput");
  const name = input.value.trim();
  if (!name) { setNameMsg("error", "Name must not be empty."); input.focus(); return; }
  if (name === nameEdit.current) { endNameEdit(); return; }   // nothing to write
  const id = nameEdit.id;
  $("#nameSave").disabled = true;
  $("#nameCancel").disabled = true;
  $("#nameSave").textContent = "Writing …";
  nameEdit.writing = true;
  setNameMsg("hint", "Writing …");
  let res;
  try {
    res = await api(`api/devices/${encodeURIComponent(id)}/name`, { method: "POST", json: { name } });
  } catch (e) {
    res = { status: 0, body: { detail: e.message } };
  }
  const { status, body } = res;
  if (status !== 200) {
    // api() does not throw on 422 (policy and settings forms need the body); here 422 is an error
    const detail = Array.isArray(body?.detail) ? body.detail.map((x) => x.msg).join("; ") : body?.detail;
    nameEdit = { id, current: nameEdit?.current ?? "" };
    $("#nameSave").disabled = false;
    $("#nameCancel").disabled = false;
    $("#nameSave").textContent = "Write to device";
    setNameMsg("error", detail || "Writing failed.");
    input.focus();
    return;
  }
  nameEdit = null;
  devState.hold += 1;
  try {
    await loadOverview();
    await loadDevicePage(id);
  } finally { devState.hold -= 1; }
  setNameMsg("ok", body.changed
    ? `Written and read back.${body.restart_required ? " The device asks for a restart." : ""}`
    : "Name unchanged.");
}

function wireNameEdit(d) {
  const input = $("#nameInput");
  if (!input) return;
  input.dataset.device = d.id;
  input.dataset.current = d.name || "";
  if (!allowRename) return;
  $("#nameEdit").addEventListener("click", () => (nameEdit ? input.focus() : startNameEdit()));
  $("#nameUsePlan")?.addEventListener("click", () => startNameEdit(input.closest(".rr-name").dataset.planned));
  $("#nameSave").addEventListener("click", saveName);
  $("#nameCancel").addEventListener("click", () => endNameEdit());
  input.addEventListener("keydown", (ev) => {
    if (!nameEdit || nameEdit.writing) return;
    if (ev.key === "Enter") { ev.preventDefault(); saveName(); }
    if (ev.key === "Escape") { ev.preventDefault(); endNameEdit(); }
  });
}

// ---- Restart ----------------------------------------------------------------------------

// As with renaming: if the function is off, the button stays visible, disabled, and leads to Settings
const rebootButton = (cls = "") => allowReboot
  ? `<button class="btn ${cls}" type="button" data-act="reboot" title="Restart the device (Shelly.Reboot) and wait until it is back">${ICON.power}Restart device</button>`
  : `<a class="btn rr-off ${cls}" href="#settings" aria-disabled="true"
       title="Restarting is off. Turn it on in Settings">${ICON.power}Restart device</a>`;

async function rebootDevice() {
  const d = devState.body?.device;
  if (!d || !allowReboot) return;
  if (!await confirmDialog(`Restart "${d.name || d.id}" now? Outputs, inputs and scripts on this device stop for a few seconds.`, "Restart device")) return;
  const btns = $$("[data-act=reboot]");
  btns.forEach((b) => { b.disabled = true; });
  const deskBtn = $("#devActions .btn[data-act=reboot]");
  if (deskBtn) deskBtn.innerHTML = `${ICON.power}Restarting …`;
  setDevMsg("hint", "Waiting for the device to come back. This can take up to 90 seconds.");
  devState.hold += 1;
  let res;
  try {
    ({ body: res } = await api(`api/devices/${encodeURIComponent(d.id)}/reboot`, { method: "POST" }));
  } catch (e) {
    devState.hold -= 1;
    btns.forEach((b) => { b.disabled = false; });
    if (deskBtn) deskBtn.innerHTML = `${ICON.power}Restart device`;
    setDevMsg("error", e.message);
    return;
  }
  try {
    await loadOverview();
    if (devState.id === d.id) await loadDevicePage(d.id);
  } finally { devState.hold -= 1; }
  if (devState.id === d.id) setDevMsg("ok", `Restarted. The device was back after ${res.seconds} s.`);
}
