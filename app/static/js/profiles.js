"use strict";
// Part of the UI, loads after app.js; shared global scope, order defined in index.html
// ---- Soll-Profil -----------------------------------------------------------------------------

let policyLoaded = false;

let policyHash = null;   // version the editor loaded

// ---- Line numbers of the editor --------------------------------------------------------------
let polLineCount = 0;
let polErrLines = new Set();     // lines the last validation named

function polLines(force) {
  const t = $("#polText"), n = t.value.split("\n").length;
  if (n !== polLineCount || force) {
    polLineCount = n;
    const box = $("#polLines");
    box.parentElement.style.width = `${Math.max(3, String(n).length) + 0.5}ch`;
    box.innerHTML = Array.from({ length: n }, (_, i) => `<span${polErrLines.has(i + 1) ? ' class="rr-line-err"' : ""}>${i + 1}</span>`).join("");
  }
  polScroll();
}

function polScroll() {
  $("#polLines").style.transform = `translateY(${-$("#polText").scrollTop}px)`;
}

// Line and column of the cursor under the editor
function polPos() {
  const t = $("#polText"), before = t.value.slice(0, t.selectionStart).split("\n");
  $("#polPos").textContent = `Line ${before.length}, column ${before.at(-1).length + 1}`;
}

// Select a line and bring it into view
function polGoto(line) {
  const t = $("#polText"), lines = t.value.split("\n");
  const n = Math.min(Math.max(1, line), lines.length);
  const start = lines.slice(0, n - 1).reduce((sum, l) => sum + l.length + 1, 0);
  t.focus();
  t.setSelectionRange(start, start + lines[n - 1].length);
  t.scrollTop = Math.max(0, (n - 1) * parseFloat(getComputedStyle(t).lineHeight) - t.clientHeight / 2);
  polScroll();
  polPos();
}

async function loadPolicy() {
  const { body } = await api("api/policy");
  $("#polText").value = body.text;
  policyHash = body.hash;
  policyLoaded = true;
  policyMessage(body.errors);
}

function policyMessage(errors, okText) {
  const box = $("#polMsg");
  polErrLines = new Set((errors || []).flatMap((e) => [...String(e).matchAll(/\bline (\d+)/g)].map((m) => +m[1])));
  polLines(true);
  if (errors?.length) {
    box.className = "alert alert-danger";
    // "line N" in a message jumps to that line
    box.innerHTML = `<h4 class="alert-title">Errors in the policy</h4><ul class="mb-0">${errors.map((e) => `<li>${
      esc(e).replace(/\bline (\d+)/g, '<a href="#policy/yaml" class="alert-link" data-pol-line="$1">line $1</a>')}</li>`).join("")}</ul>`;
  } else if (okText) {
    box.className = "alert alert-success";
    box.textContent = okText;
  } else {
    box.className = "alert d-none";
  }
}

async function checkPolicy() {
  const { body } = await api("api/policy/validate", { method: "POST", json: { text: $("#polText").value } });
  policyMessage(body.errors, body.ok ? "Valid." : "");
}

async function savePolicy() {
  const { status, body } = await api("api/policy", { method: "PUT", json: { text: $("#polText").value, base_hash: policyHash }, keep: "all" });
  if (status === 409) {
    policyMessage(body.errors);
    if (await confirmDialog(`${body.errors[0]} Load the current version? Your edits in the editor are discarded.`, "Load current version", true)) await loadPolicy();
    return;
  }
  if (status >= 400) { policyMessage(body.errors || [body.detail]); return; }
  policyHash = body.hash;
  prof.loaded = false;   // the rules tab reads the file again
  policyMessage([], "Saved. Findings have been recalculated.");
  await loadOverview();
}

// ---- Rules: baseline and profiles -------------------------------------------------------
// Manage the baseline and profiles without YAML. They are still stored in policy.yaml (round trip on the server,
// comments stay). #policy = rules list, #policy/baseline, #policy/profile/new, #policy/profile/<name>,
// #policy/overrides, #policy/yaml. Old addresses #profiles[/<name>] are redirected.

const MATCH_FIELDS = [["kind", "Function"], ["variant", "Form factor"], ["app", "App code"], ["model", "Model"], ["profile", "Device profile"], ["gen", "Generation"], ["name", "Device name"], ["id", "Device ID"]];
const RULE_OPS = [["=", "="], ["in", "one of"], ["not", "not"], ["min", "≥ min"], ["max", "≤ max"], ["expr", "expression"]];
let prof = { loaded: false, list: [], baseline: null, overrides: [], choices: {}, templates: [], devices: [], errors: [], edit: null, notice: null };
let profTimer = null;

const profHref = (name) => name == null ? "#policy" : `#policy/profile/${encodeURIComponent(name)}`;
function polRoute() {
  const parts = location.hash.slice(1).split("?")[0].split("/");
  if (parts[0] !== "policy") return { view: "rules", name: null };
  if (parts[1] === "profile" && parts.length > 2 && parts[2] !== "") return { view: "profile", name: decodeURIComponent(parts.slice(2).join("/")) };
  if (["baseline", "overrides", "yaml"].includes(parts[1])) return { view: parts[1], name: null };
  return { view: "rules", name: null };
}
// The former Profiles page: #profiles -> #policy, #profiles/<name> -> #policy/profile/<name>
function legacyPolicyHash() {
  const h = location.hash;
  if (!/^#profiles(\/|$|\?)/.test(h)) return;
  const rest = h.slice("#profiles/".length).split("?")[0];
  history.replaceState(null, "", rest ? `#policy/profile/${rest}` : "#policy");
}

// Values as text in the inputs: lists comma-separated, text without quotes
const scalarText = (v) => typeof v === "string" ? v : JSON.stringify(v);
function ruleText(r) {
  if (r.op === "in") return (Array.isArray(r.value) ? r.value : [r.value]).map(scalarText).join(", ");
  if (r.op === "expr") return JSON.stringify(r.value);
  return scalarText(r.value);
}
function parseScalar(t) {
  const s = t.trim();
  if (s === "true") return true;
  if (s === "false") return false;
  if (s === "null") return null;
  if (/^-?\d+(\.\d+)?$/.test(s)) return Number(s);
  if (/^".*"$/.test(s)) { try { return JSON.parse(s); } catch { /* Text */ } }
  return s;
}
function parseRule(text, op) {
  if (op === "in") return text.split(",").map((x) => x.trim()).filter((x) => x !== "").map(parseScalar);
  if (op === "expr") { try { return JSON.parse(text); } catch { throw new Error(`Expression is not valid JSON: ${text}`); } }
  return parseScalar(text);
}

function profDraft(p) {
  const match = {};
  for (const [k] of MATCH_FIELDS) {
    const v = p?.match?.[k];
    match[k] = v == null ? "" : Array.isArray(v) ? v.join(", ") : String(v);
  }
  return {
    name: p?.name || "",
    match,
    settings: (p?.settings || []).map((r) => ({ path: r.path, op: r.op, text: ruleText(r), comment: r.comment || "" })),
    ignore: (p?.ignore || []).join(", "),
    devices: [...(p?.assigned || [])],      // assignment, in the list the field is called assigned
    exclude: [...(p?.exclude || [])],
  };
}
function profFromDraft(d) {
  const match = {};
  for (const [k] of MATCH_FIELDS) {
    const parts = (d.match[k] || "").split(",").map((x) => x.trim()).filter(Boolean);
    if (parts.length) match[k] = parts.length > 1 ? parts : parts[0];
  }
  return {
    name: d.name.trim(),
    match,
    settings: d.settings.map((r) => ({ path: r.path.trim(), op: r.op, value: parseRule(r.text, r.op), comment: r.comment.trim() })),
    ignore: d.ignore.split(",").map((x) => x.trim()).filter(Boolean),
    devices: d.devices,
    exclude: d.exclude,
  };
}

const matchShort = (m) => Object.entries(m || {}).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(" | ") : v}`).join(" · ") || "–";
const devName = (id) => { const d = prof.devices.find((x) => x.id === id); return d ? (d.name || d.id) : id; };

async function loadProfiles() {
  const { body } = await api("api/profiles");
  Object.assign(prof, { loaded: true, list: body.profiles, baseline: body.baseline, overrides: body.overrides || [], choices: body.choices,
    templates: body.templates, devices: body.devices, errors: body.errors });
}

async function showPolicy() {
  const { view, name } = polRoute();
  const tab = view === "baseline" || view === "profile" ? "rules" : view;
  $$("#polTabs [data-pol-tab]").forEach((a) => {
    const on = a.dataset.polTab === tab;
    a.classList.toggle("active", on);
    a.setAttribute("aria-selected", String(on));
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  show($("#profPage"), tab === "rules");
  show($("#polOverrides"), tab === "overrides");
  show($("#polYaml"), tab === "yaml");
  if (tab !== "rules") { prof.edit = null; profHeader(PAGES.policy.pre, PAGES.policy.title, PAGES.policy.sub); }
  if (tab === "yaml") { if (!policyLoaded) await loadPolicy(); return; }
  if (!prof.loaded) await loadProfiles();
  const now = polRoute();
  if (currentPage !== "policy" || now.view !== view || now.name !== name) return;   // navigated on in the meantime
  if (tab === "overrides") { renderOverrides(); return; }
  if (view === "rules" || prof.errors.length) { prof.edit = null; renderProfileList(); return; }
  const route = view === "baseline" ? "#baseline" : name;   // "#" cannot start a profile name in the URL
  if (!prof.edit || prof.edit.route !== route) {
    if (view === "baseline") {
      prof.edit = { route, baseline: true, original: null, missing: !prof.baseline, draft: profDraft(prof.baseline), preview: null, errors: [], busy: false };
    } else {
      const original = route === "new" ? null : route;
      const p = original === null ? null : prof.list.find((x) => x.name === original);
      prof.edit = { route, original, missing: original !== null && !p, draft: profDraft(p || prof.pendingCopy), preview: null, errors: [], busy: false };
      prof.pendingCopy = null;
    }
  }
  renderProfileEditor();
}

function profHeader(pre, title, sub) {
  $("#pagePre").textContent = pre;
  $("#pageTitle").textContent = title;
  $("#pageSub").innerHTML = sub;
  document.title = `${title} · RelayRoster`;
}

function profNotice() {
  if (!prof.notice) return "";
  const n = prof.notice;
  prof.notice = null;
  return `<div class="alert alert-${n.kind === "ok" ? "success" : "danger"}" role="status">${n.html}</div>`;
}

const policyInvalid = (what) => `<div class="alert alert-danger"><h4 class="alert-title">policy.yaml is invalid</h4>
  ${what} can be edited again once the file is fixed in the <a href="#policy/yaml">YAML</a> tab.
  <ul class="mb-0 mt-2">${prof.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>`;

const devCount = (ids) => ids.length ? `<span title="${esc(ids.map(devName).join(", "))}">${ids.length}</span>` : `<span class="text-secondary">0</span>`;
const findCount = (n) => n ? `<a href="#drift">${badge("warn", n)}</a>` : badge("ok", "0");

function renderProfileList() {
  profHeader(PAGES.policy.pre, PAGES.policy.title, PAGES.policy.sub);
  if (prof.errors.length) { $("#profPage").innerHTML = policyInvalid("The baseline and profiles"); return; }
  const b = prof.baseline;
  const base = `<tr>
      <td class="text-secondary">–</td>
      <td><a href="#policy/baseline">Baseline</a></td>
      <td class="text-secondary">all devices</td>
      <td>${devCount(b.devices)}</td>
      <td>${b.settings.length}</td>
      <td>${findCount(b.findings)}</td>
      <td class="text-end text-nowrap"><a class="btn btn-sm" href="#policy/baseline">Edit</a></td></tr>`;
  const n = prof.list.length;
  const empty = `<tr><td></td><td colspan="6" class="text-secondary">No profiles yet. A profile sets the target values for one function, e.g. all roller shutters. <a href="#policy/profile/new">New profile</a></td></tr>`;
  const rows = base + (n ? "" : empty) + prof.list.map((p, i) => `<tr>
      <td class="text-secondary">${i + 1}</td>
      <td><a href="${profHref(p.name)}">${esc(p.name)}</a></td>
      <td class="font-monospace small">${esc(matchShort(p.match))}</td>
      <td>${devCount(p.devices)}</td>
      <td>${p.settings.length}</td>
      <td>${findCount(p.findings)}</td>
      <td class="text-end text-nowrap">
        <button class="btn btn-sm btn-icon" type="button" data-prof-move="${i}" data-dir="-1" aria-label="Move ${esc(p.name)} up" ${i === 0 ? "disabled" : ""}>↑</button>
        <button class="btn btn-sm btn-icon" type="button" data-prof-move="${i}" data-dir="1" aria-label="Move ${esc(p.name)} down" ${i === n - 1 ? "disabled" : ""}>↓</button>
        <a class="btn btn-sm" href="${profHref(p.name)}">Edit</a>
        <button class="btn btn-sm" type="button" data-prof-apply="${i}" ${p.devices.length ? "" : "disabled"}>Apply…</button>
        <button class="btn btn-sm" type="button" data-prof-copy="${i}">Duplicate</button>
        <button class="btn btn-sm btn-ghost-danger" type="button" data-prof-del="${i}">Delete</button>
      </td></tr>`).join("");
  $("#profPage").innerHTML = `${profNotice()}<div class="card">${cardTable(["#", "Name", "Match", "Devices", "Rules", "Findings", ""], rows)}
    <div class="card-footer text-secondary small">Evaluated top to bottom: the baseline for every device, then each matching profile. If two set the same path, the lower one wins; a device <a href="#policy/overrides">override</a> wins over all of them. “Apply…” writes the fixed values of a profile to its devices, with a preview first.</div></div>`;
  $$("[data-prof-move]").forEach((b) => b.addEventListener("click", () => moveProfile(+b.dataset.profMove, +b.dataset.dir)));
  $$("[data-prof-copy]").forEach((b) => b.addEventListener("click", () => {
    const p = prof.list[+b.dataset.profCopy];
    let name = `${p.name} copy`, k = 2;
    while (prof.list.some((x) => x.name === name)) name = `${p.name} copy ${k++}`;
    prof.pendingCopy = { ...p, name };
    prof.edit = null;
    location.hash = "#policy/profile/new";
  }));
  $$("[data-prof-del]").forEach((b) => b.addEventListener("click", () => deleteProfile(prof.list[+b.dataset.profDel])));
  $$("[data-prof-apply]").forEach((b) => b.addEventListener("click", () => applyProfile(prof.list[+b.dataset.profApply].name)));
}

async function profWrite(path, method, json) {
  const { status, body } = await api(path, { method, json, keep: "all" });
  if (status >= 400) throw new Error((body.errors || [body.detail || "Failed"]).join(" · "));
  policyLoaded = false;   // reload the YAML editor next time it opens
  await loadProfiles();
  loadOverview().catch((e) => showError(e.message));
  return body;
}

async function moveProfile(i, dir) {
  const names = prof.list.map((p) => p.name);
  const j = i + dir;
  [names[i], names[j]] = [names[j], names[i]];
  try { await profWrite("api/profiles/order", "POST", { names }); }
  catch (e) { prof.notice = { kind: "bad", html: esc(e.message) }; }
  renderProfileList();
}

async function deleteProfile(p) {
  if (!await confirmDialog(`Delete profile "${p.name}"? Its rules no longer apply to ${p.devices.length} device(s). policy.yaml is backed up first.`, "Delete profile", true)) return;
  try {
    await profWrite(`api/profiles/${encodeURIComponent(p.name)}`, "DELETE");
    prof.notice = { kind: "ok", html: `Profile <b>${esc(p.name)}</b> deleted.` };
  } catch (e) { prof.notice = { kind: "bad", html: esc(e.message) }; }
  renderProfileList();
}

function choiceOptions(key) {
  return (prof.choices[key] || []).map((c) => `<option value="${esc(c.value)}">${esc(c.label ? `${c.label} (${c.count})` : `${c.count} device${c.count === 1 ? "" : "s"}`)}</option>`).join("");
}

function ruleRow(r, i) {
  return `<tr data-rule="${i}">
    <td><input class="form-control form-control-sm font-monospace" list="pfPaths" data-f="path" value="${esc(r.path)}" placeholder="cover:*.power_limit" aria-label="Path"></td>
    <td><select class="form-select form-select-sm" data-f="op" aria-label="Operator">${RULE_OPS.map(([v, l]) => `<option value="${v}"${v === r.op ? " selected" : ""}>${l}</option>`).join("")}</select></td>
    <td><input class="form-control form-control-sm font-monospace" data-f="text" value="${esc(r.text)}" placeholder="${r.op === "in" ? "a, b" : "value"}" aria-label="Value"></td>
    <td><input class="form-control form-control-sm" data-f="comment" value="${esc(r.comment)}" placeholder="why" aria-label="Comment"></td>
    <td class="text-end"><button class="btn btn-sm btn-ghost-danger btn-icon" type="button" data-rule-del="${i}" aria-label="Remove rule">✕</button></td>
  </tr><tr class="rr-muted-row" data-rule-res="${i}"><td colspan="5" class="small pt-0" id="pfRes-${i}"></td></tr>`;
}

function renderProfileEditor() {
  const e = prof.edit, d = e.draft;
  if (e.baseline) profHeader("Target state / Policy", "Baseline", "Target values for every device. A profile can replace a rule for its devices or ignore it, an override does the same for one device. Nothing is written to a device; the preview shows the current values and the findings that result.");
  else profHeader("Target state / Policy", e.original === null ? "New profile" : `Profile: ${e.original}`, "Target values for one function. Nothing is written to a device; the preview shows which devices match and which findings result.");
  if (e.missing) {
    $("#profPage").innerHTML = `<div class="card"><div class="empty"><p class="empty-title">Profile not found</p>
      <p class="empty-subtitle text-secondary">${esc(e.original)}</p><div class="empty-action"><a class="btn" href="#policy">All rules</a></div></div></div>`;
    return;
  }
  const tpl = e.original === null && !e.baseline && prof.templates.length ? `<div class="mb-3"><label class="form-label" for="pfTpl">Start from template</label>
      <select class="form-select" id="pfTpl"><option value="">Empty</option>${prof.templates.map((t, i) => `<option value="${i}">${esc(t.name)}</option>`).join("")}</select></div>` : "";
  const left = e.baseline ? "" : `
      <div class="col-lg-5">
        <div class="card"><div class="card-header"><h3 class="card-title">Profile</h3></div><div class="card-body">
          ${tpl}
          <div class="mb-3"><label class="form-label required" for="pfName">Name</label>
            <input class="form-control" id="pfName" value="${esc(d.name)}" autocomplete="off"></div>
          <h4 class="mb-1">Match</h4>
          <p class="text-secondary small mb-2">All filled fields must match. <code>kind</code> is what the hardware does (<code>2PM</code>, <code>i4</code>) and holds across generations. Globs allowed (<code>shellyplus1-*</code>), several values separated by commas mean “or”.</p>
          ${MATCH_FIELDS.map(([k, l]) => `<div class="mb-2 row g-2 align-items-center"><label class="col-4 col-form-label py-0" for="pfm-${k}">${l} <span class="text-secondary small font-monospace">${k}</span></label>
            <div class="col-8"><input class="form-control form-control-sm font-monospace" id="pfm-${k}" data-match="${k}" list="pfc-${k}" value="${esc(d.match[k])}" autocomplete="off">
            <datalist id="pfc-${k}">${choiceOptions(k)}</datalist></div></div>`).join("")}
          <div id="pfAssign" class="mt-3"></div>
          <div class="mt-3"><label class="form-label" for="pfIgnore">Ignore baseline rules</label>
            <input class="form-control form-control-sm font-monospace" id="pfIgnore" value="${esc(d.ignore)}" placeholder="wifi.ap.enable" autocomplete="off">
            <div class="form-hint">Paths from the baseline that do not apply to these devices, separated by commas.</div></div>
        </div></div>
      </div>`;
  $("#profPage").innerHTML = `${profNotice()}<div id="pfErr"></div>
    <div class="row row-cards">${left}
      <div class="${e.baseline ? "col-12" : "col-lg-7"}">
        <div class="card"><div class="card-header"><h3 class="card-title" id="pfMatchTitle">Matches – devices</h3></div>
          <div class="card-body" id="pfMatches"><span class="text-secondary">…</span></div></div>
      </div>
      <div class="col-12">
        <div class="card"><div class="card-header"><h3 class="card-title">Settings</h3>
          <div class="card-actions"><button class="btn btn-sm" type="button" id="pfAdd">Add rule</button></div></div>
          <div class="table-responsive"><table class="table table-vcenter card-table rr-rules">
            <thead><tr><th style="width:32%">Path</th><th style="width:12%">Operator</th><th style="width:20%">Value</th><th>Comment</th><th></th></tr></thead>
            <tbody id="pfRules"></tbody></table></div>
          <datalist id="pfPaths"></datalist>
          <div class="card-footer text-secondary small">Paths follow <code>Shelly.GetConfig</code>; suggestions come from the matching devices. Below each rule: the current value per device.</div>
        </div>
      </div>
      <div class="col-12"><div class="card"><div class="card-header"><h3 class="card-title" id="pfFindTitle">Findings from this ${e.baseline ? "baseline" : "profile"}</h3></div><div id="pfFindings"><div class="card-body text-secondary">…</div></div></div></div>
    </div>
    <div class="d-flex gap-2 justify-content-end mt-3">
      <a class="btn" href="#policy">Cancel</a>
      <button class="btn" type="button" id="pfValidate">Validate</button>
      <button class="btn btn-primary" type="button" id="pfSave">Save</button>
    </div>`;
  renderRules();
  if (!e.baseline) {
    renderAssign();
    $("#pfName").addEventListener("input", (ev) => { d.name = ev.target.value; schedulePreview(); });
    $("#pfIgnore").addEventListener("input", (ev) => { d.ignore = ev.target.value; schedulePreview(); });
  }
  $$("[data-match]").forEach((el) => el.addEventListener("input", () => { d.match[el.dataset.match] = el.value; schedulePreview(); }));
  $("#pfTpl")?.addEventListener("change", (ev) => {
    const t = prof.templates[+ev.target.value];
    const name = d.name;
    e.draft = profDraft(t || null);
    if (!t) e.draft.name = name;
    renderProfileEditor();
  });
  $("#pfAdd").addEventListener("click", () => { d.settings.push({ path: "", op: "=", text: "", comment: "" }); renderRules(); $(`[data-rule="${d.settings.length - 1}"] [data-f="path"]`)?.focus(); });
  $("#pfValidate").addEventListener("click", () => runPreview(true));
  $("#pfSave").addEventListener("click", saveProfile);
  runPreview();
}

// Assignment in the profile editor: devices in addition to match, or excluded despite match
function renderAssign() {
  const d = prof.edit.draft;
  const chip = (id, list) => `<span class="badge bg-azure-lt rr-chip me-1 mb-1">${esc(devName(id))}<button type="button" data-chip="${list}" data-id="${esc(id)}" aria-label="Remove ${esc(devName(id))}">×</button></span>`;
  const opts = prof.devices.filter((x) => !d.devices.includes(x.id) && !d.exclude.includes(x.id))
    .map((x) => `<option value="${esc(x.id)}">${esc(x.name || x.id)} · ${esc(x.model_name || x.app)}</option>`).join("");
  $("#pfAssign").innerHTML = `
    <label class="form-label">Assigned devices</label>
    <div>${d.devices.map((id) => chip(id, "devices")).join("") || `<span class="text-secondary small">none</span>`}</div>
    <div class="form-hint mb-1">Belong to this profile even if Match does not fit.</div>
    <label class="form-label mt-2">Excluded devices</label>
    <div>${d.exclude.map((id) => chip(id, "exclude")).join("") || `<span class="text-secondary small">none</span>`}</div>
    <div class="form-hint mb-1">Never get this profile, even if Match fits.</div>
    <div class="input-group input-group-sm mt-2"><select class="form-select" id="pfAssignDev"><option value="">Device…</option>${opts}</select>
      <button class="btn" type="button" data-assign-to="devices">Assign</button><button class="btn" type="button" data-assign-to="exclude">Exclude</button></div>`;
  $$("[data-chip]", $("#pfAssign")).forEach((b) => b.addEventListener("click", () => {
    const list = d[b.dataset.chip];
    list.splice(list.indexOf(b.dataset.id), 1);
    renderAssign(); schedulePreview();
  }));
  $$("[data-assign-to]", $("#pfAssign")).forEach((b) => b.addEventListener("click", () => {
    const id = $("#pfAssignDev").value;
    if (!id) return;
    d[b.dataset.assignTo].push(id);
    renderAssign(); schedulePreview();
  }));
}

function renderRules() {
  const d = prof.edit.draft;
  $("#pfRules").innerHTML = d.settings.length ? d.settings.map(ruleRow).join("")
    : `<tr><td colspan="5" class="text-secondary">No rules yet.</td></tr>`;
  $$("#pfRules [data-rule]").forEach((tr) => {
    const r = d.settings[+tr.dataset.rule];
    $$("[data-f]", tr).forEach((el) => el.addEventListener(el.tagName === "SELECT" ? "change" : "input", () => { r[el.dataset.f] = el.value; schedulePreview(); }));
  });
  $$("[data-rule-del]").forEach((b) => b.addEventListener("click", () => { d.settings.splice(+b.dataset.ruleDel, 1); renderRules(); schedulePreview(); }));
  renderPreview();
}

function schedulePreview() {
  if (prof.edit) prof.edit.saveErrors = [];
  clearTimeout(profTimer);
  profTimer = setTimeout(() => runPreview(), 300);
}

function setProfErrors(previewErrors) {
  const box = $("#pfErr");
  if (!box) return;
  const errors = [...new Set([...(previewErrors || []), ...(prof.edit?.saveErrors || [])])];
  box.innerHTML = errors.length ? `<div class="alert alert-danger"><ul class="mb-0">${errors.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : "";
}

async function runPreview(announce = false) {
  const e = prof.edit;
  if (!e || e.missing) return;
  let profile;
  try { profile = profFromDraft(e.draft); } catch (err) { e.errors = [err.message]; setProfErrors(e.errors); return; }
  const seq = (e.seq = (e.seq || 0) + 1);
  const { status, body } = e.baseline
    ? await api("api/baseline/preview", { method: "POST", json: { settings: profile.settings }, keep: "all" })
    : await api("api/profiles/preview", { method: "POST", json: { profile, original: e.original }, keep: "all" });
  if (prof.edit !== e || seq !== e.seq) return;   // typed on in the meantime
  if (status >= 400) {
    e.errors = body.errors || [body.detail || "Preview failed"];
    setProfErrors(e.errors);
    return;
  }
  e.errors = [];
  e.preview = body;
  setProfErrors([]);
  if (announce) $("#pfErr").innerHTML = `<div class="alert alert-success" role="status">Valid. ${body.devices.length} device(s) ${e.baseline ? "covered" : "match"}, ${body.findings.length} finding(s).</div>`;
  renderPreview();
}

function renderPreview() {
  const e = prof.edit, pv = e?.preview;
  if (!pv || !$("#pfMatches")) return;
  $("#pfMatchTitle").textContent = `${e.baseline ? "Applies to" : "Matches"} ${pv.devices.length} device${pv.devices.length === 1 ? "" : "s"}`;
  $("#pfMatches").innerHTML = pv.devices.length ? `<div class="d-flex flex-wrap gap-2">${pv.devices.map((d) =>
    `<a class="badge bg-azure-lt text-decoration-none" href="${devHref(d.id)}" title="${esc(d.id)} · ${esc(d.host)}">${esc(d.name || d.id)}<span class="text-secondary ms-1">${esc(d.model_name || d.app)}</span></a>`).join("")}</div>`
    : `<span class="text-secondary">No device matches. Pick a value from the suggestions in the match fields.</span>`;
  $("#pfPaths").innerHTML = pv.paths.map((p) => `<option value="${esc(p)}"></option>`).join("");
  const byPath = Object.fromEntries(pv.rules.map((r) => [r.path, r]));
  e.draft.settings.forEach((r, i) => {
    const cell = $(`#pfRes-${i}`);
    if (!cell) return;
    const res = byPath[r.path.trim()];
    if (!res || !r.path.trim()) { cell.innerHTML = ""; return; }
    const state = { nodata: ["neutral", "no backup yet"], na: ["neutral", "component not present"], missing: ["warn", "setting missing"] };
    cell.innerHTML = res.devices.map((x) => {
      const name = esc(devName(x.id));
      if (state[x.state]) return badge(state[x.state][0], `${devName(x.id)}: ${state[x.state][1]}`);
      return x.values.map((v) => `<span class="badge ${v.ok ? BADGE.ok : BADGE.bad} me-1" title="${esc(v.path)}">${name}${x.values.length > 1 ? ` ${esc(v.path.split(".")[0])}` : ""}: ${esc(fmtVal(v.actual))}</span>`).join("");
    }).join(" ") || `<span class="text-secondary">no matching device</span>`;
  });
  $("#pfFindTitle").textContent = `Findings from this ${e.baseline ? "baseline" : "profile"} (${pv.findings.length})`;
  $("#pfFindings").innerHTML = pv.findings.length
    ? cardTable(["Device", "Path", "Expected", "Actual"], pv.findings.map((f) => `<tr><td><a href="${devHref(f.device_id)}">${esc(f.device_name || f.device_id)}</a></td>
        <td class="font-monospace small">${esc(f.path)}</td><td>${valHtml(f.expected)}</td><td>${f.kind === "missing" ? `<span class="text-secondary">not present</span>` : valHtml(f.actual)}</td></tr>`).join(""))
    : `<div class="card-body text-secondary">${pv.devices.length ? `All ${e.baseline ? "devices meet the baseline" : "matching devices meet the profile"}.` : "–"}</div>`;
}

async function saveProfile() {
  const e = prof.edit;
  let profile;
  try { profile = profFromDraft(e.draft); } catch (err) { setProfErrors([err.message]); return; }
  $("#pfSave").disabled = true;
  try {
    const body = e.baseline ? await profWrite("api/baseline", "PUT", { settings: profile.settings })
      : e.original === null
      ? await profWrite("api/profiles", "POST", { profile })
      : await profWrite(`api/profiles/${encodeURIComponent(e.original)}`, "PUT", { profile });
    prof.edit = null;
    prof.notice = { kind: "ok", html: `Saved to <code>policy.yaml</code>, the previous version is kept as a backup. Findings have been recalculated.` };
    const target = e.baseline ? "#policy/baseline" : profHref(body.name);
    if (location.hash === target) showPolicy(); else location.hash = target;
  } catch (err) {
    e.saveErrors = err.message.split(" · ");
    setProfErrors(e.errors);
    $("#pfSave").disabled = false;
  }
}

// ---- Overrides -------------------------------------------------------------------------
// Read only: overrides are set on the device page (findings) or in the YAML tab.

function renderOverrides() {
  const box = $("#polOverrides");
  if (prof.errors.length) { box.innerHTML = policyInvalid("Overrides"); return; }
  if (!prof.overrides.length) {
    box.innerHTML = `<div class="card"><div class="empty"><p class="empty-title">No overrides</p>
      <p class="empty-subtitle text-secondary">An override sets a value for a single device, e.g. a measured power limit. Create one from a finding on the device page.</p></div></div>`;
    return;
  }
  const known = new Set(prof.devices.map((d) => d.id));
  const OP_SIGN = { in: "one of", not: "not", min: "≥", max: "≤" };
  const opText = (r) => OP_SIGN[r.op] ? `${OP_SIGN[r.op]} ${ruleText(r)}` : ruleText(r);
  const rows = prof.overrides.flatMap((o) => {
    const dev = known.has(o.id) ? `<a href="${devHref(o.id, "findings")}">${esc(devName(o.id))}</a>`
      : `${esc(o.id)} ${badge("neutral", "not found")}`;
    const lines = [...o.settings.map((r) => [esc(r.path), `<span class="font-monospace">${esc(opText(r))}</span>`, esc(r.comment)]),
      ...o.ignore.map((x) => [esc(x), `<span class="text-secondary">ignored</span>`, ""])];
    if (!lines.length) lines.push(["", `<span class="text-secondary">no rules</span>`, ""]);
    return lines.map((l, i) => `<tr>${i === 0 ? `<td rowspan="${lines.length}">${dev}</td>` : ""}<td class="font-monospace small">${l[0]}</td><td>${l[1]}</td><td class="text-secondary small">${l[2]}</td></tr>`);
  }).join("");
  box.innerHTML = `<div class="card">${cardTable(["Device", "Path", "Target", "Comment"], rows)}
    <div class="card-footer text-secondary small">An override wins over the baseline and every profile, for this one device. Create or remove one from the findings on the device page, or edit it in the <a href="#policy/yaml">YAML</a> tab.</div></div>`;
}
