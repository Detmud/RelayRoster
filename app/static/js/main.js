"use strict";
// Part of the UI, loads after app.js; shared global scope, order defined in index.html
// ---- Verdrahtung -----------------------------------------------------------------------------

// Pages: sidebar entry, page header and content. sync: false hides "Sync now" on pages without device data. The hash names are the same as for the old tabs,
// so existing links (#drift, #devices?sort=…) keep working.
const PAGES = {
  dashboard: { pre: "Overview", title: "Dashboard", sub: "State of all devices at a glance. Every tile opens the matching view." },
  devices: { pre: "Roster", title: "Devices", sub: "All Shellies found on the network. A click on a row opens the device page; Ctrl/Cmd-click on the name opens it in a new tab." },
  device: { pre: "Roster / Devices", title: "Device", sub: "" },   // content comes from renderDeviceHeader
  drift: { pre: "Roster", title: "Findings", sub: "Deviations from the policy. The source shows which rule applies: baseline, a type profile, a device override or the address plan." },
  peers: { pre: "Roster", title: "Peer comparison", sub: "Device types whose devices differ from each other, without any policy. Highlighted values differ from the majority. Settings without effect (e.g. <code>cloud.server</code> with cloud off) do not count, see <code>peer_depends</code>. Device-specific paths are listed under <code>peer_ignore</code>; paths in <code>ignore</code> of a profile or override do not count for its devices." },
  firmware: { pre: "Roster", title: "Firmware", sub: "Installed version per device, grouped by type. \"Stable available\" is reported by the device itself." },
  backups: { pre: "Roster", title: "Backups", sub: "RelayRoster stores a snapshot on every change: configuration, scripts with code, schedules, webhooks and KVS. A firmware update alone does not count as a change." },
  profiles: { pre: "Configuration", title: "Profiles", sync: false, sub: "Target values per function, e.g. all roller shutters, stored in <code>/data/policy.yaml</code> with comments kept. Nothing here is written to a device." },
  activity: { pre: "Roster", title: "Activity", sync: false, sub: "Everything RelayRoster wrote: names, firmware, restarts, settings, passwords, plus changes to Settings and the policy. Newest first." },
  bulk: { pre: "Roster", title: "Bulk change", sub: "Set one setting on many devices at once, e.g. the NTP server for all devices. Preview first; devices without the setting are left alone." },
  policy: { pre: "Configuration", title: "Policy", sync: false, sub: "The target state for all devices, stored in <code>/data/policy.yaml</code>. Changes here only affect the comparison, never a device." },
  settings: { pre: "Configuration", title: "Settings", sync: false, sub: "Stored in <code>/data</code> and applied immediately, no restart. Environment variables only provide the defaults." },
};
let currentPage = null;

// push: create a browser history entry (click in the menu), otherwise only adjust the address
function showPage(name, push = false) {
  const changed = name !== currentPage;
  if (changed && currentPage === "device") endNameEdit(false);
  currentPage = name;
  const p = PAGES[name];
  const navName = name === "device" ? "devices" : name;   // the device page belongs to Devices
  $$("#sidebar .nav-item[data-page]").forEach((li) => {
    const on = li.dataset.page === navName;
    li.classList.toggle("active", on);
    if (on) $(".nav-link", li).setAttribute("aria-current", "page");
    else $(".nav-link", li).removeAttribute("aria-current");
  });
  $$(".rr-tab").forEach((el) => show(el, el.id === `tab-${name}`));
  $("#syncBtn").title = name === "device" ? "Read this device now" : "Read all known devices now";
  $$("[data-actions]").forEach((el) => show(el, el.dataset.actions === name));
  $$("#syncGroup, .rr-syncinfo").forEach((el) => show(el, p.sync !== false));
  if (name === "device") {
    showDevice(devRoute()).catch((e) => showError(e.message));
  } else if (name === "profiles") {
    showProfiles().catch((e) => showError(e.message));
  } else {
    if (name === "bulk") showBulk().catch((e) => showError(e.message));
    if (name === "activity") showAudit().catch((e) => showError(e.message));
    $("#pagePre").textContent = p.pre;
    $("#pageTitle").textContent = p.title;
    $("#pageSub").innerHTML = p.sub;
    document.title = `${p.title} · RelayRoster`;
  }
  if (name === "policy" && !policyLoaded) loadPolicy().catch((e) => showError(e.message));
  if (name === "settings" && !settingsLoaded) loadSettings().catch((e) => showError(e.message));
  const hash = name === "devices" ? viewToHash() : name === "device" || (name === "profiles" && location.hash.startsWith("#profiles"))
    ? location.hash : `#${name}`;
  if (location.hash !== hash) history[push ? "pushState" : "replaceState"](null, "", hash);
  if (changed && push) window.scrollTo(0, 0);
  setNav(false);
}

const pageFromHash = () => {
  let name = location.hash.slice(1).split("?")[0].split("/")[0];
  if (name === "audit") name = "activity";   // old name of the page, links keep working
  if (name === "device") return devRoute() ? "device" : null;
  return Object.hasOwn(PAGES, name) ? name : null;
};

// Hash changes from links (tiles, types) and back/forward in the browser
function onLocation() {
  const page = pageFromHash() || "dashboard";
  if (page === "devices") { view = loadView(); renderDevices(); }
  showPage(page);
}

// Below 992 px the sidebar is a navbar with hamburger (Tabler collapse, here without Tabler JS)
function setNav(open) {
  const menu = $("#sidebarMenu");
  if (!menu) return;
  menu.classList.toggle("show", open);
  $("#navToggle").setAttribute("aria-expanded", String(open));
  $("#navToggle").setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
}

document.addEventListener("DOMContentLoaded", () => {
  // Menu and brand: own history entry, the device list keeps its filters
  $$("a[data-page]").forEach((a) => a.addEventListener("click", (ev) => {
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;  // neuer Tab: Browser machen lassen
    ev.preventDefault();
    // Profiles in the menu: always the list, also from an open profile
    if (a.dataset.page === "profiles" && location.hash !== "#profiles") { history.pushState(null, "", "#profiles"); prof.edit = null; }
    showPage(a.dataset.page, true);
  }));
  $("#navToggle").addEventListener("click", () => setNav(!$("#sidebarMenu").classList.contains("show")));
  $("#signOut").addEventListener("click", (ev) => { ev.preventDefault(); signOut(); });
  wireCfg();
  window.addEventListener("hashchange", onLocation);
  window.addEventListener("popstate", onLocation);
  $("#syncBtn").addEventListener("click", () => (currentPage === "device" && devState.id ? syncDevice(devState.id) : refresh(false)));
  $("#scanBtn").addEventListener("click", () => { setSyncMenu(false); refresh(true); });
  $("#syncMenuBtn").addEventListener("click", () => setSyncMenu(!$("#syncMenu").classList.contains("show")));
  $$("#syncMenu .dropdown-item").forEach((i) => i.addEventListener("click", () => setSyncMenu(false)));
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && $("#syncMenu").classList.contains("show")) { setSyncMenu(false); $("#syncMenuBtn").focus(); }
    if (ev.key === "Escape" && $("#sidebarMenu").classList.contains("show")) { setNav(false); $("#navToggle").focus(); }
    if (ev.key === "Escape") $$(".rr-menu.show").forEach((m) => { setMenu(m, false); m.previousElementSibling?.focus(); });
  });
  $("#cancelBtn").addEventListener("click", cancelScan);
  $("#devFilter").addEventListener("input", (ev) => setView({ q: ev.target.value }));
  for (const [k, sel] of Object.entries(FILTERS)) $(sel).addEventListener("change", (ev) => setView({ [k]: ev.target.value }));
  $$("#devTable .table-sort").forEach((b) => b.addEventListener("click", () => {
    const key = b.dataset.sort;
    // First click: problems on top. Deviations descending; status (errors first) and Wi-Fi (weakest first) ascending
    const firstDir = key === "findings" ? "desc" : "asc";
    setView(view.sort === key ? { dir: view.dir === "asc" ? "desc" : "asc" } : { sort: key, dir: firstDir });
  }));
  $$("#attnTable .table-sort").forEach((b) => b.addEventListener("click", () => {
    const key = b.dataset.sort;
    attnSort = attnSort.key === key ? { key, dir: attnSort.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" };
    render();
  }));
  const resetFilters = () => setView({ ...VIEW_DEFAULT, sort: view.sort, dir: view.dir });
  $("#devReset").addEventListener("click", resetFilters);
  $("#devReset2").addEventListener("click", resetFilters);
  $$("#driftChips .btn").forEach((c) => c.addEventListener("click", () => {
    driftKind = c.dataset.k;
    $$("#driftChips .btn").forEach((x) => x.classList.toggle("btn-primary", x === c));
    renderDrift();
  }));
  document.addEventListener("click", (ev) => {
    if (!ev.target.closest("#syncGroup")) setSyncMenu(false);
    // Menus with data-menu-toggle (device page): open and close, a click elsewhere closes
    const toggle = ev.target.closest("[data-menu-toggle]");
    $$(".rr-menu.show").forEach((m) => { if (!toggle || m !== toggle.nextElementSibling) setMenu(m, false); });
    if (toggle) { setMenu(toggle.nextElementSibling, !toggle.nextElementSibling.classList.contains("show")); return; }
    const act = ev.target.closest("[data-act]");
    if (act) {
      if (act.dataset.act === "reboot") rebootDevice();
      if (act.dataset.act === "remove") removeDevice();
      return;
    }
    const peerGoto = ev.target.closest("[data-peer-goto]");
    if (peerGoto) {
      peerSel = peerGoto.dataset.peerGoto;
      peerPeek = null;
      try { localStorage.setItem("relayroster.peer", peerSel); } catch { /* egal */ }
      renderPeers();
      return;   // the link itself changes the page
    }
    const fix = ev.target.closest("[data-fix]");
    if (fix) {
      const ids = fixGroups().get(fix.dataset.fix) || [];
      const f = data.findings.find((x) => x.kind === "mismatch" && fixKey(x) === fix.dataset.fix);
      if (f && ids.length) fixAll(f.path, f.target, ids);
      return;
    }
    const staleDel = ev.target.closest("[data-stale-del]");
    if (staleDel) { removeStaleRule(data.stale_rules[+staleDel.dataset.staleDel]).catch((e) => showError(e.message)); return; }
    const unassign = ev.target.closest("[data-unassign]");
    if (unassign) {
      if (confirm(`Remove this device from profile ${unassign.dataset.unassign}? If its match rule fits, the device is added to the profile's exclude list.`))
        assignDevice(unassign.dataset.unassign, false).catch((e) => showError(e.message));
      return;
    }
    const applyProf = ev.target.closest("[data-apply-prof]");
    if (applyProf) { applyProfile(applyProf.dataset.applyProf, [devState.id]); return; }
    const fwOpen = ev.target.closest("[data-fw-open]");
    if (fwOpen) {
      if (fwWasRunning) openFwJob(); else openFwDialog(fwOpen.dataset.fwOpen);
      return;
    }
    if (ev.target.closest("[data-stop]")) return;  // links in rows do not also open the row
    // Click on the rest of the row: device page in the same tab, Back leads to the list
    const row = ev.target.closest("tr[data-id]");
    if (row) goDevice(row.dataset.id, row.dataset.section);
  });
  $$("#planDlg [data-close]").forEach((b) => b.addEventListener("click", () => $("#planDlg").close()));
  $("#planDlg").addEventListener("click", (ev) => { if (ev.target === ev.currentTarget) ev.currentTarget.close(); });
  $("#planApply").addEventListener("click", applyPlan);
  $$("#fwDlg [data-close]").forEach((b) => b.addEventListener("click", () => $("#fwDlg").close()));
  $("#fwDlg").addEventListener("click", (ev) => { if (ev.target === ev.currentTarget) ev.currentTarget.close(); });
  $("#fwStart").addEventListener("click", startFwUpdate);
  $$("#applyDlg [data-close]").forEach((b) => b.addEventListener("click", () => $("#applyDlg").close()));
  $("#applyDlg").addEventListener("click", (ev) => { if (ev.target === ev.currentTarget) ev.currentTarget.close(); });
  $("#applyStart").addEventListener("click", startApply);
  $("#applyStop").addEventListener("click", stopApply);
  $("#devBulk").addEventListener("click", () => {
    bulk.preset = sortedDevices(data.devices.filter(matches)).map((d) => d.id);
    location.hash = "#bulk";
  });
  document.addEventListener("change", (ev) => {
    const sel = ev.target.closest("[data-assign]");
    if (sel && sel.value) assignDevice(sel.value, true).catch((e) => showError(e.message));
  });
  $("#fwCancel").addEventListener("click", cancelFwJob);
  $("#polCheck").addEventListener("click", () => checkPolicy().catch((e) => showError(e.message)));
  $("#polSave").addEventListener("click", () => savePolicy().catch((e) => showError(e.message)));
  $("#setSave").addEventListener("click", () => saveSettings().catch((e) => showError(e.message)));
  $("#setForm").addEventListener("submit", (ev) => { ev.preventDefault(); saveSettings().catch((e) => showError(e.message)); });
  $("#polText").addEventListener("keydown", (ev) => {
    if (ev.key === "Tab") {
      ev.preventDefault();
      const t = ev.target, s = t.selectionStart;
      t.setRangeText("  ", s, t.selectionEnd, "end");
    }
    if ((ev.metaKey || ev.ctrlKey) && ev.key === "s") { ev.preventDefault(); savePolicy(); }
  });

  // Without a hash the dashboard starts. The device list keeps remembering its view in localStorage
  view = loadView();
  showPage(pageFromHash() || "dashboard");
  syncViewControls();
  loadOverview().catch((e) => showError(e.message));
  pollState();
});
