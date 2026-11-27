# Architecture

A FastAPI backend with an SQLite database and a static frontend without a build step.

```
app/
  main.py              FastAPI: all endpoints, sign-in/out, CSRF header check, static files without caching
  auth.py              Signed session cookie after OIDC sign-in
  oidc.py              OIDC client: discovery, PKCE, token exchange, ID token check against JWKS, groups
  config.py            Settings from environment variables (also NAME_FILE for secrets)
  runtime_settings.py  Overrides from the UI stored in the database (meta.settings), validation
  collector.py         Scan and periodic reads, rename, restart, write configuration; owns the firmware updater
  address.py           Address job: static address from the address plan, wait for the device, audit
  firmware.py          Firmware job: identity, CheckForUpdate, Update, wait for restart, audit
  notify.py            Notifications via ntfy/webhook: offline/online, new devices, new findings, failed writes
  apply.py             Apply profile and bulk change: preview per device, background job via set_config
  shelly.py            RPC client with allow list (is_read_only, is_rename_call, is_firmware_call, is_reboot_call, …)
  writable.py          Allow list for editing: fields per component, SetConfig methods, value checks, building calls
  discovery.py         HTTP scan (GET /shelly), target planning, at most /16 per network
  store.py             SQLite: devices, snapshots, audit, meta
  policy.py            Parse and validate the policy, evaluate rules, address plan, naming plan
  policy_edit.py       Change profiles and overrides in policy.yaml (ruamel.yaml round trip, backups)
  profiles_view.py     Policy page (baseline, profiles): counters, suggestions, live preview
  compare.py           Path matching, diffs, peer groups (peer_depends, READ_ONLY_PATHS, MANAGED_PATHS), versions
  devicepage.py        Device page: schema per component (type, unit, options), settings view, peer outliers
  report.py            Overview for the UI (findings, firmware), classifying missing settings
  models.py            Order number to device type (kind, variant, suffix), MAC format
  default_policy.yaml  Template, copied to /data/policy.yaml on first start
  static/              index.html, login.html, app.js (core) and js/*.js per area, style.css, theme.js
tests/
  fake_shelly.py       Simulated fleet as an httpx transport (including update, restart, connection loss)
  test_*.py            Core logic, HTTP API, profiles, apply/bulk, OIDC against a simulated provider, vendor fetch
  demo.py              App with the simulated fleet, for UI work and screenshots
  ci.sh                ruff and all tests in their own venv
tools/fetch_vendor.py  Downloads Tabler CSS from npm and checks the SHA-256
```

## Frontend

Tabler admin layout with a sidebar (`#sidebar`). Pages are `<div id="tab-<name>" class="rr-tab">`; title and
description live in `PAGES` in `static/js/main.js`, page actions in the header via `data-actions="<name>"`. Routing
uses the URL hash (`#devices?sort=…`). The device page is the only dynamic page: `#device/<id>[/<tab>]`.
Own CSS classes and variables in `static/style.css` carry the `rr-` prefix (RelayRoster) to keep them apart from Tabler.


Script order is defined in `index.html`; all files share one global scope. New page: entry in `PAGES`, `nav-item` in
the sidebar, pane `#tab-<name>`, script in `index.html`.

All asset and API paths are relative (`static/`, `api/`), so the app also works under a sub-path behind a proxy.

## Important API endpoints

`/api/state`, `/auth/login|callback`, `/api/overview`, `/api/devices/{id}`, `/api/refresh?scan=`, `/api/scan/cancel`,
`/api/devices/{id}/name`, `/api/devices/{id}/reboot`, `/api/devices/{id}/config[/preview]`, `/api/names/plan|apply`,
`/api/firmware/update|job|job/cancel`, `/api/snapshots/{id}/diff|download`, `/api/backup.zip`, `/api/policy`,
`/api/profiles[/{name}]|preview|order`, `/api/devices/{id}/override`, `/api/apply[/preview]`, `/api/apply/job[/cancel]`,
`/api/settings`, `/api/notify/test`. State-changing calls require `X-Requested-With: relayroster`.

## Testing notes

A patch on `time.monotonic` freezes the event loop; the collector uses `_clock()` for that. Background jobs in tests
need `with TestClient(app) as c`, otherwise the loop ends between requests.
