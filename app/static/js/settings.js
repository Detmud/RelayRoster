"use strict";
// Part of the UI, loads after app.js; shared global scope, order defined in index.html
// ---- Settings -----------------------------------------------------------------------

let settingsData = null;
let settingsLoaded = false;
let clearPasswords = new Set();   // password fields marked for removal

const fmtSetting = (f, v) => f.kind === "list" ? (v?.length ? v.join(", ") : "(none)")
  : f.kind === "bool" ? (v ? "on" : "off") : f.kind === "url" ? (v || "(none)") : `${v}${f.unit ? ` ${f.unit}` : ""}`;

function settingInput(f) {
  const id = `set-${f.key}`;
  if (f.kind === "bool") {
    return `<label class="form-check form-switch mb-0"><input id="${id}" class="form-check-input" type="checkbox"${f.value ? " checked" : ""}>
      <span class="form-check-label">${esc(f.label)}</span></label>`;
  }
  if (f.kind === "list") {
    return `<textarea id="${id}" class="form-control font-monospace" rows="${Math.max(2, (f.value?.length || 0) + 1)}" spellcheck="false">${esc((f.value || []).join("\n"))}</textarea>`;
  }
  if (f.kind === "password") {
    const ph = f.is_set ? "Set. Leave empty to keep it." : "Not set";
    return `<div class="input-group"><input id="${id}" class="form-control" type="password" autocomplete="new-password" placeholder="${esc(ph)}">
      ${f.is_set ? `<button class="btn" type="button" data-clear="${f.key}">Remove</button>` : ""}</div>`;
  }
  if (f.kind === "url") {
    return `<input id="${id}" class="form-control font-monospace" type="url" spellcheck="false" placeholder="https://…" value="${esc(f.value || "")}">`;
  }
  const step = f.kind === "float" ? "0.1" : "1";
  const input = `<input id="${id}" class="form-control" type="number" inputmode="decimal" step="${step}" min="${f.min ?? ""}" max="${f.max ?? ""}" value="${esc(f.value)}">`;
  return f.unit ? `<div class="input-group">${input}<span class="input-group-text">${esc(f.unit)}</span></div>` : input;
}

function settingHint(f) {
  const def = f.kind === "password" ? (f.default_is_set ? "set" : "not set") : fmtSetting(f, f.default);
  const reset = f.overridden ? ` · <a href="#settings" data-reset="${f.key}">Reset to default</a>` : "";
  const src = f.overridden ? badge("accent", "changed here") : badge("neutral", "default");
  return `<div class="form-hint">${esc(f.help)}</div>
    <div class="form-hint">${src} Default from <code>${esc(f.env)}</code>: ${esc(def)}${reset}</div>`;
}

function renderSettings(errors = {}) {
  const groups = [];
  for (const f of settingsData.fields) {
    let g = groups.find((x) => x.name === f.group);
    if (!g) groups.push(g = { name: f.group, fields: [] });
    g.fields.push(f);
  }
  clearPasswords = new Set();
  $("#setForm").innerHTML = groups.map((g) => `<fieldset class="mb-4"><h3 class="mb-3">${esc(g.name)}</h3>
    ${g.fields.map((f) => `<div class="mb-3" data-field="${f.key}">
      ${f.kind === "bool" ? "" : `<label class="form-label" for="set-${f.key}">${esc(f.label)}</label>`}
      ${settingInput(f)}
      <div class="invalid-feedback d-block${errors[f.key] ? "" : " d-none"}">${esc(errors[f.key] || "")}</div>
      ${settingHint(f)}
    </div>`).join("")}${g.name === "Notifications" ? notifyBlock() : ""}</fieldset>`).join("")
    + `<p class="text-secondary small mb-0">Stay in the environment: ${settingsData.env_only.map((e) => `<code>${esc(e)}</code>`).join(", ")}.</p>`;
  for (const [k, msg] of Object.entries(errors)) {
    if (msg) $(`#set-${k}`)?.classList.add("is-invalid");
  }
  $$("#setForm [data-clear]").forEach((btn) => btn.addEventListener("click", () => {
    const key = btn.dataset.clear;
    const on = !clearPasswords.has(key);
    if (on) clearPasswords.add(key); else clearPasswords.delete(key);
    btn.classList.toggle("btn-danger", on);
    btn.textContent = on ? "Will be removed" : "Remove";
    $(`#set-${key}`).value = "";
    $(`#set-${key}`).disabled = on;
  }));
  $("#notifyTest")?.addEventListener("click", () => sendTestNotification().catch((e) => showError(e.message)));
  $$("#setForm [data-reset]").forEach((a) => a.addEventListener("click", (ev) => {
    ev.preventDefault();
    saveSettings({ [a.dataset.reset]: null }).catch((e) => showError(e.message));
  }));
}

// Notifications: last delivery and test button, below the notification settings
function notifyResultText(results) {
  return Object.entries(results || {}).map(([t, r]) => `${t}: ${r.ok ? "delivered" : `failed (${r.error})`}`).join(" · ");
}

function notifyBlock() {
  const st = settingsData.notify || {};
  const last = st.last;
  const lastText = last
    ? `Last ${last.test ? "test message" : "message"} ${esc(fmtTime(last.at))}: ${esc(notifyResultText(last.results))}`
    : "Nothing sent yet.";
  return `<div class="mb-3" id="notifyStatus">
    <button class="btn" type="button" id="notifyTest"${st.targets?.length ? "" : " disabled"}>Send test notification</button>
    <div class="form-hint">${st.targets?.length ? "Uses the saved URLs. Save changes first." : "Save an ntfy topic URL or a webhook URL first."}</div>
    <div class="form-hint">${lastText}</div>
  </div>`;
}

async function sendTestNotification() {
  const { status, body } = await api("api/notify/test", { method: "POST", keep: "all" });
  if (status === 400) { settingsMessage("warning", esc(body.detail)); return; }
  if (body.notify) settingsData.notify = body.notify;
  renderSettings();
  settingsMessage(body.ok ? "success" : "danger", `Test notification: ${esc(notifyResultText(body.results))}`);
}

function settingsMessage(kind, html) {
  const box = $("#setMsg");
  box.className = kind ? `alert alert-${kind}` : "alert d-none";
  box.innerHTML = html || "";
}

async function loadSettings() {
  const { body } = await api("api/settings");
  settingsData = body;
  settingsLoaded = true;
  renderSettings();
}

function collectSettings() {
  const out = {};
  for (const f of settingsData.fields) {
    const el = $(`#set-${f.key}`);
    if (!el) continue;
    let v;
    if (f.kind === "bool") v = el.checked;
    else if (f.kind === "url") v = el.value.trim();
    else if (f.kind === "list") v = el.value.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);
    else if (f.kind === "password") {
      if (clearPasswords.has(f.key)) { out[f.key] = ""; continue; }
      if (!el.value) continue;
      v = el.value;
    } else v = el.value.trim() === "" ? el.value : Number(el.value);
    if (f.kind !== "password" && JSON.stringify(v) === JSON.stringify(f.value)) continue;
    out[f.key] = v;
  }
  return out;
}

async function saveSettings(changes) {
  changes = changes || collectSettings();
  if (!Object.keys(changes).length) { settingsMessage("info", "No changes."); return; }
  const { status, body } = await api("api/settings", { method: "PUT", json: { settings: changes } });
  if (status === 422) {
    settingsMessage("danger", "Not saved. Check the highlighted fields.");
    const keep = settingsData;
    // Keep the inputs, only mark the errors
    for (const [k, msg] of Object.entries(body.errors)) {
      const box = $(`[data-field="${k}"] .invalid-feedback`);
      if (box) { box.textContent = msg; show(box, true); $(`#set-${k}`)?.classList.add("is-invalid"); }
    }
    const unknown = Object.keys(body.errors).filter((k) => !keep.fields.some((f) => f.key === k));
    if (unknown.length) settingsMessage("danger", `Not saved: ${unknown.map((k) => esc(`${k}: ${body.errors[k]}`)).join("; ")}`);
    return;
  }
  settingsData = body;
  renderSettings();
  const changed = body.changed || [];
  let note = changed.length ? "Saved and applied." : "Saved. Nothing changed.";
  if (changed.some((k) => k === "scan_networks" || k === "extra_hosts")) note += " Run <strong>Scan</strong> to search the new targets now.";
  if (changed.includes("poll_interval_min")) note += " The next sync follows the new interval.";
  settingsMessage("success", note);
  await pollState();
}

// ---- Abmelden ---------------------------------------------------------------------------

async function signOut() {
  try { await api("api/logout", { method: "POST" }); } catch { /* does not matter, the sign-in page follows anyway */ }
  location.replace("login");
}
