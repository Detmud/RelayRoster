"""RelayRoster: web app and API."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import re
import secrets
import threading
import zipfile
from contextlib import asynccontextmanager
from html import escape
from typing import Any
from urllib.parse import urlencode

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import address as addressmod, apply as applymod, compare, devicepage, policy as pol, policy_edit as pe, profiles_view, report, writable
from .auth import COOKIE, FLOW_COOKIE, FLOW_MAX_AGE, MAX_AGE, Auth
from .collector import Collector, ConfigError, ConfigInvalid, RebootError, RenameError
from .config import Settings, settings as default_settings
from .models import format_mac, model_name, type_fields
from .oidc import OIDC, OIDCError, pkce_pair
from .runtime_settings import RuntimeSettings, SettingsError
from .store import Store
from .version import VERSION

STATIC = os.path.join(os.path.dirname(__file__), "static")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
# Relative paths (static/…), so RelayRoster also works under a sub-path behind a proxy
_ASSET = re.compile(r'(static/(?:app\.js|style\.css|theme\.js|js/[a-z-]+\.js))"')
_SIDEBAR = 'class="navbar navbar-vertical navbar-expand-lg d-print-none" id="sidebar"'
# Return after the OIDC sign-in: only a hash of our own UI (#devices?sort=…), never a foreign address
_NEXT = re.compile(r"#[A-Za-z0-9_\-/?=&.%:,+~]{0,300}")


def render_login(html: str, authn: Auth, cfg: Settings) -> str:
    """Sign-in page with the button for the identity provider. The only way to sign in."""
    if "<!--SSO-->" not in html:
        raise RuntimeError("login.html: placeholder <!--SSO--> not found")
    sso = ""
    if authn.oidc_enabled:
        sso = (f'<a id="ssoBtn" class="btn btn-primary w-100" href="auth/login">'
               f'Sign in with {escape(cfg.oidc_provider_name)}</a>')
    return html.replace("<!--SSO-->", sso)


def create_app(cfg: Settings | None = None, transport=None, autostart: bool = True,
               oidc_transport=None) -> FastAPI:
    cfg = cfg or default_settings
    logging.basicConfig(level=cfg.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.makedirs(cfg.data_dir, exist_ok=True)
    if not os.path.exists(os.path.join(STATIC, "vendor", "tabler.min.css")):
        logging.getLogger(__name__).warning(
            "Tabler CSS missing, the UI runs without styles. Locally: python tools/fetch_vendor.py")
    store = Store(cfg.db_path)
    pol.ensure_file(cfg.policy_path)
    # Apply overrides from the UI before the collector, so the very first run uses them
    runtime = RuntimeSettings(cfg, store)
    collector = Collector(store, cfg, transport=transport)
    authn = Auth(cfg, store)
    oidc = OIDC(cfg, transport=oidc_transport)
    if authn.oidc_enabled and not cfg.oidc_client_secret:
        logging.getLogger(__name__).warning("OIDC_CLIENT_SECRET missing: token request only as a public client with PKCE")
    if not cfg.oidc_issuer:
        logging.getLogger(__name__).warning(
            "No sign-in configured (OIDC_ISSUER empty): anyone who can reach this port can use RelayRoster. "
            "See docs/security.md.")
    background: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if autostart:
            collector.start()
        yield
        await collector.stop()

    # OpenAPI and Swagger UI only behind sign-in, hence the built-in routes are off
    app = FastAPI(title="RelayRoster", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.store = store
    app.state.collector = collector
    app.state.settings = cfg
    app.state.runtime = runtime
    app.state.auth = authn
    app.state.oidc = oidc

    def auth(request: Request) -> None:
        # Without WWW-Authenticate: no browser popup, the UI redirects to the sign-in page itself
        if authn.current_user(request) is None:
            raise HTTPException(401, "Authentication required")

    def csrf(request: Request) -> None:
        # State-changing calls only with our own header: a foreign page cannot set it without CORS permission
        if request.headers.get("x-requested-with") != "relayroster":
            raise HTTPException(403, "Header X-Requested-With missing")

    @app.get("/api/openapi.json", include_in_schema=False, dependencies=[Depends(auth)])
    def openapi_json() -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/api/docs", include_in_schema=False, dependencies=[Depends(auth)])
    def api_docs() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="openapi.json", title="RelayRoster API")

    def load_policy() -> tuple[dict[str, Any] | None, list[str]]:
        try:
            return pol.load(cfg.policy_path), []
        except pol.PolicyError as exc:
            return None, exc.errors

    def peer_depends(policy_data: dict[str, Any] | None) -> list[dict[str, Any]]:
        return (policy_data or {}).get("peer_depends", pol.DEFAULT_PEER_DEPENDS)

    def peer_groups(policy_data: dict[str, Any] | None, rep: dict[str, Any]) -> list[dict[str, Any]]:
        ignore = (policy_data or {}).get("peer_ignore") or []
        devs = store.devices()
        dev_ignore = {d["id"]: pol.device_ignore(policy_data, d) for d in devs} if policy_data else {}
        groups = compare.peer_groups(devs, rep["configs"], ignore, peer_depends(policy_data), dev_ignore)
        for g in groups:
            for r in (*g["diffs"], *g.get("hidden", [])):
                r["label"] = devicepage.setting_label(r["path"])
            for r in g["diffs"]:
                r["editable"] = writable.is_editable(r["path"])
        return groups

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def static_revalidate(request: Request, call_next):
        # Without Cache-Control the browser caches app.js heuristically and does not ask for an update.
        # no-cache means: always revalidate via ETag, unchanged returns 304.
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        # Security headers: no embedding in foreign pages, no MIME sniffing, no referrer to other sites
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'; object-src 'none'; base-uri 'self'")
        return response

    # Version parameter on our own assets: every new version has new URLs
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
        index_html = _ASSET.sub(rf'\1?v={VERSION}"', fh.read())
    with open(os.path.join(STATIC, "login.html"), encoding="utf-8") as fh:
        login_html = render_login(_ASSET.sub(rf'\1?v={VERSION}"', fh.read()), authn, cfg)
    # Set the sidebar layout while serving, so nothing jumps while loading
    index_folded = index_html.replace(_SIDEBAR, _SIDEBAR.replace("navbar-expand-lg", "navbar-expand-lg navbar-folded-hover"))
    if index_folded == index_html:
        raise RuntimeError("index.html: sidebar markup not found")

    @app.get("/", include_in_schema=False)
    def index(request: Request) -> Response:
        if authn.current_user(request) is None:
            # Relative, so it also works under a sub-path. The browser keeps the hash (#devices …)
            return RedirectResponse("login", status_code=303, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(index_folded if cfg.sidebar_folded else index_html, headers={"Cache-Control": "no-cache"})

    # ---- Sign-in ------------------------------------------------------------------------

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request) -> Response:
        if not authn.enabled or authn.verify(request.cookies.get(COOKIE)):
            return RedirectResponse("./", status_code=303)
        return HTMLResponse(login_html, headers={"Cache-Control": "no-cache"})

    @app.post("/api/logout", dependencies=[Depends(csrf)])
    def logout() -> JSONResponse:
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    # ---- OIDC ---------------------------------------------------------------------------

    def redirect_uri(request: Request) -> str:
        if cfg.oidc_redirect_uri:
            return cfg.oidc_redirect_uri
        # Behind a reverse proxy: scheme and host from the forwarded headers, otherwise from the request
        proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
        host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc)
        path = request.url.path.rsplit("/auth/", 1)[0] + "/auth/callback"
        return f"{proto}://{host.split(',')[0].strip()}{path}"

    def back_to_login(error: str) -> RedirectResponse:
        resp = RedirectResponse("../login?" + urlencode({"error": error}), status_code=303,
                                headers={"Cache-Control": "no-store"})
        resp.delete_cookie(FLOW_COOKIE, path="/")
        return resp

    @app.get("/auth/login", include_in_schema=False)
    async def oidc_login(request: Request, next: str = "") -> Response:  # noqa: A002
        if not authn.oidc_enabled:
            raise HTTPException(404, "OIDC is not configured")
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier, challenge = pkce_pair()
        target = redirect_uri(request)
        try:
            url = await oidc.authorize_url(redirect_uri=target, state=state, nonce=nonce, challenge=challenge)
        except OIDCError as exc:
            logging.getLogger(__name__).warning("OIDC: %s", exc)
            return back_to_login(str(exc))
        flow = {"state": state, "nonce": nonce, "verifier": verifier, "redirect_uri": target,
                "next": next if _NEXT.fullmatch(next) else ""}
        resp = RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})
        # SameSite=Lax: the browser sends the cookie on the return from the IdP (top-level GET)
        resp.set_cookie(FLOW_COOKIE, authn.seal(flow), max_age=FLOW_MAX_AGE, path="/", httponly=True,
                        samesite="lax", secure=authn.secure(request))
        return resp

    @app.get("/auth/callback", include_in_schema=False)
    async def oidc_callback(request: Request, code: str = "", state: str = "", error: str = "",
                            error_description: str = "") -> Response:
        if not authn.oidc_enabled:
            raise HTTPException(404, "OIDC is not configured")
        log = logging.getLogger(__name__)
        flow = authn.unseal(request.cookies.get(FLOW_COOKIE))
        if error:
            log.warning("OIDC: Provider meldet %s %s", error, error_description)
            return back_to_login(f"{cfg.oidc_provider_name}: {error_description or error}"[:200])
        if not flow:
            return back_to_login("Sign-in took too long or started in another browser. Try again.")
        if not state or not secrets.compare_digest(state, str(flow.get("state", ""))):
            log.warning("OIDC: state does not match")
            return back_to_login("Sign-in check failed (state). Try again.")
        if not code:
            return back_to_login("No authorization code received.")
        try:
            tokens = await oidc.exchange(code=code, verifier=flow["verifier"], redirect_uri=flow["redirect_uri"])
            claims = await oidc.validate(tokens["id_token"], flow["nonce"])
            user = oidc.user_from(claims)
        except OIDCError as exc:
            log.warning("OIDC: sign-in rejected: %s", exc)
            return back_to_login(str(exc))
        log.info("OIDC: %s angemeldet", user)
        resp = RedirectResponse("../" + flow.get("next", ""), status_code=303, headers={"Cache-Control": "no-store"})
        resp.delete_cookie(FLOW_COOKIE, path="/")
        resp.set_cookie(COOKIE, authn.issue(user, source="oidc"), max_age=MAX_AGE, path="/", httponly=True,
                        samesite="lax", secure=authn.secure(request))
        return resp

    # ---- Status and runs -----------------------------------------------------------------------

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state(request: Request) -> dict[str, Any]:
        st = collector.state
        return {
            "version": VERSION,
            "auth": authn.enabled,
            "user": authn.current_user(request),
            "running": st.running,
            "phase": st.phase,
            "done": st.done,
            "total": st.total,
            "found": st.found,
            "elapsed_s": st.elapsed_seconds(),
            "eta_s": st.eta_seconds(),
            "cancelling": st.cancelling,
            "last_started": st.last_started,
            "last_finished": st.last_finished,
            "last_scan": st.last_scan,
            "last_summary": st.last_summary,
            "last_error": st.last_error,
            "networks": cfg.scan_networks,
            "extra_hosts": cfg.extra_hosts,
            "scan_targets": [str(n) for n in collector.plan.networks] + collector.plan.hosts,
            "scan_addresses": collector.plan.total,
            "scan_warnings": collector.plan.warnings + ([] if cfg.scan_networks or cfg.extra_hosts else [
                "No networks configured yet. Add the network your Shelly devices are in, e.g. 192.168.1.0/24."]),
            "auto_scan": cfg.scan_every_n_polls > 0,
            "poll_interval_min": cfg.poll_interval_min,
            "read_only": not (cfg.allow_firmware_update or cfg.allow_reboot or cfg.allow_config
                              or cfg.allow_set_password or cfg.allow_address),
            "allow_address": cfg.allow_address,
            "address_job": _address_summary(),
            "allow_firmware_update": cfg.allow_firmware_update,
            "firmware_parallel": cfg.firmware_parallel,
            "allow_reboot": cfg.allow_reboot,
            "allow_set_password": cfg.allow_set_password,
            "allow_config": cfg.allow_config,
            "sidebar_folded": cfg.sidebar_folded,
            "firmware_job": _job_summary(),
            "apply_job": _apply_summary(),
        }

    def _address_summary() -> dict[str, Any] | None:
        job = collector.address.job
        if not job:
            return None
        v = job.view()
        return {k: v[k] for k in ("id", "running", "cancelled", "total", "done", "counts", "current", "finished_at")}

    def _apply_summary() -> dict[str, Any] | None:
        job = collector.apply.job
        if not job:
            return None
        v = job.view()
        return {k: v[k] for k in ("id", "title", "running", "cancelled", "total", "done", "counts", "current",
                                  "finished_at")}

    def _job_summary() -> dict[str, Any] | None:
        job = collector.firmware.job
        if not job:
            return None
        v = job.view()
        return {k: v[k] for k in ("id", "stage", "running", "cancelled", "total", "done", "active", "counts",
                                  "current", "finished_at")}

    @app.post("/api/refresh", status_code=202, dependencies=[Depends(auth), Depends(csrf)])
    async def refresh(scan: bool = False) -> dict[str, Any]:
        if collector.state.running:
            return {"started": False, "reason": "already running"}
        # Keep a reference: asyncio only holds tasks weakly, a long /16 scan must not be collected
        task = asyncio.create_task(collector.run(scan=scan))
        background.add(task)
        task.add_done_callback(background.discard)
        return {"started": True, "scan": scan}

    @app.post("/api/devices/{device_id}/refresh", dependencies=[Depends(auth), Depends(csrf)])
    async def refresh_device(device_id: str) -> dict[str, Any]:
        """Read only one device again."""
        try:
            return {"device_id": device_id, **await collector.poll_device(device_id)}
        except KeyError:
            raise HTTPException(404, "Unknown device") from None

    @app.post("/api/scan/cancel", dependencies=[Depends(auth), Depends(csrf)])
    def cancel_scan() -> dict[str, Any]:
        return {"cancelled": collector.cancel_scan()}

    # ---- Overviews ----------------------------------------------------------------------------

    @app.get("/api/overview", dependencies=[Depends(auth)])
    def overview() -> dict[str, Any]:
        policy_data, policy_errors = load_policy()
        rep = report.build(store, policy_data, cfg.poll_interval_min)
        peers = peer_groups(policy_data, rep)
        return {
            "devices": rep["devices"],
            "findings": rep["findings"],
            "peers": peers,
            "firmware": report.firmware_view(rep["devices"], rep["newest"]),
            "policy_errors": policy_errors,
            "names_unknown": rep["names_unknown"],
            "stale_rules": rep["stale_rules"],
            "kind_hints": pol.kind_hints(policy_data, store.devices()) if policy_data else [],
        }

    @app.get("/api/devices/{device_id}", dependencies=[Depends(auth)])
    def device_detail(device_id: str) -> dict[str, Any]:
        dev = store.device(device_id)
        if not dev:
            raise HTTPException(404, "Unknown device")
        policy_data, _ = load_policy()
        snap = store.latest_snapshot(device_id)
        # Same evaluation as the overview, so address suggestions (handed out in turn) match
        rep = report.build(store, policy_data, cfg.poll_interval_min)
        findings = [f for f in rep["findings"] if f["device_id"] == device_id]
        rules = pol.effective_rules(policy_data, dev) if policy_data else {}
        blk = pol.address_block(policy_data, dev) if policy_data else None
        # Device page: configuration per component and deviations from its own type
        config = (snap or {}).get("data", {}).get("config") or {}
        concrete = pol.resolve(policy_data, dev, compare.flatten(config))[0] if policy_data and config else {}
        peer = devicepage.peer_view(peer_groups(policy_data, rep), device_id)
        settings = devicepage.settings_view(
            config, concrete=concrete, depends=peer_depends(policy_data), outliers=devicepage.outlier_map(peer))
        row = next((r for r in rep["devices"] if r["id"] == device_id), {})
        raw_dev = dev
        dev = {**dev, "mac": format_mac(dev.get("mac")), **type_fields(dev),
               "updates": row.get("updates"), "stale": row.get("stale"), "has_snapshot": row.get("has_snapshot"),
               "profiles": row.get("profiles") or [], "caps": row.get("caps")}
        # Assignment: per profile, whether it applies via match, assignment or not at all
        assign = [{"name": p.get("name"), "via": pol.assignment(p, raw_dev)} for p in (policy_data or {}).get(
            "profiles") or []]
        return {
            "device": dev,
            "peer": peer,
            "settings": settings,
            "covers": devicepage.cover_view(config, dev.get("status")),
            "planned_name": ((policy_data or {}).get("names") or {}).get(device_id),
            "audit": store.audit(device_id),
            "address_block": {"name": blk.get("name"), "range": str(blk["range"])} if blk else None,
            "findings": findings,
            "rules": [{"path": p, "expected": pol.describe_expected(v), "raw": v, "source": s}
                      for p, (v, s) in sorted(rules.items())],
            "snapshot": snap,
            "snapshots": store.snapshots(device_id),
            "profile_assignment": assign,
            "methods": (store.methods_entry(device_id) or {}).get("methods") or None,   # Originalschreibweise
            "not_applicable": [n for n in rep["not_applicable"] if n["device_id"] == device_id],
        }

    @app.delete("/api/devices/{device_id}", dependencies=[Depends(auth), Depends(csrf)])
    def forget(device_id: str) -> dict[str, Any]:
        if not store.forget_device(device_id):
            raise HTTPException(404, "Unknown device")
        return {"forgotten": device_id}

    # ---- Umbenennen ---------------------------------------------------------------------

    def rename_allowed() -> None:
        # The name is a normal setting: same switch as all configuration changes
        if not cfg.allow_config:
            raise HTTPException(403, "Config changes are disabled. Turn on 'Allow config changes' in Settings.")

    async def _rename(device_id: str, name: Any) -> dict[str, Any]:
        try:
            return {"device_id": device_id, **await collector.rename(device_id, name)}
        except KeyError:
            raise HTTPException(404, "Unknown device") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except RenameError as exc:
            raise HTTPException(502, str(exc)) from None

    @app.post("/api/devices/{device_id}/name", dependencies=[Depends(auth), Depends(csrf), Depends(rename_allowed)])
    async def rename_device(device_id: str, name: Any = Body(..., embed=True)) -> dict[str, Any]:
        return await _rename(device_id, name)

    def _name_plan() -> list[dict[str, Any]]:
        policy_data, errors = load_policy()
        if errors:
            raise HTTPException(409, "The policy is invalid, fix it first.")
        out = []
        for dev in store.devices():
            planned = (policy_data.get("names") or {}).get(dev["id"])
            if planned is not None and planned != dev.get("name"):
                out.append({"device_id": dev["id"], "host": dev["host"], "app": dev.get("app"),
                            "model_name": model_name(dev.get("model"), dev.get("app"), dev.get("gen")),
                            "current": dev.get("name"), "planned": planned})
        return sorted(out, key=lambda x: x["planned"].lower())

    @app.get("/api/names/plan", dependencies=[Depends(auth)])
    def name_plan() -> dict[str, Any]:
        return {"changes": _name_plan(), "allow_config": cfg.allow_config}

    @app.post("/api/names/apply", dependencies=[Depends(auth), Depends(csrf), Depends(rename_allowed)])
    async def apply_names(device_ids: list[str] = Body(..., embed=True)) -> dict[str, Any]:
        """Writes the names from the plan, one after the other, only for the confirmed devices."""
        plan = {c["device_id"]: c for c in _name_plan()}
        results = []
        for dev_id in device_ids:
            if dev_id not in plan:
                results.append({"device_id": dev_id, "ok": False, "error": "Not part of the current naming plan"})
                continue
            try:
                results.append(await _rename(dev_id, plan[dev_id]["planned"]))
            except HTTPException as exc:
                results.append({"device_id": dev_id, "ok": False, "error": exc.detail})
        return {"results": results, "ok": sum(1 for r in results if r.get("ok")), "failed": sum(1 for r in results if not r.get("ok"))}

    # ---- Firmware-Updates ---------------------------------------------------------------

    def firmware_allowed() -> None:
        if not cfg.allow_firmware_update:
            raise HTTPException(403, "Firmware updates are disabled. Turn on 'Allow firmware updates' in Settings.")

    @app.get("/api/firmware/job", dependencies=[Depends(auth)])
    def firmware_job() -> dict[str, Any]:
        job = collector.firmware.job
        return {"job": job.view() if job else None, "allow_firmware_update": cfg.allow_firmware_update}

    @app.post("/api/firmware/update", status_code=202,
              dependencies=[Depends(auth), Depends(csrf), Depends(firmware_allowed)])
    async def firmware_update(device_ids: list[str] = Body(..., embed=True),
                        stage: str = Body("stable", embed=True)) -> dict[str, Any]:
        """Starts an update job. Devices run one after the other, the status comes via /api/firmware/job."""
        try:
            job = collector.firmware.start(device_ids, stage)
        except KeyError as exc:
            raise HTTPException(404, f"Unknown device {exc.args[0]}") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"job": job.view()}

    @app.post("/api/firmware/job/cancel", dependencies=[Depends(auth), Depends(csrf)])
    def firmware_cancel() -> dict[str, Any]:
        return {"cancelled": collector.firmware.cancel()}

    # ---- Address plan: static address ---------------------------------------------------

    def address_allowed() -> None:
        if not cfg.allow_address:
            raise HTTPException(403, "Address changes are disabled. Turn on 'Allow address changes' in Settings.")

    @app.get("/api/address/job", dependencies=[Depends(auth)])
    def address_job() -> dict[str, Any]:
        job = collector.address.job
        return {"job": job.view() if job else None, "allow_address": cfg.allow_address}

    @app.post("/api/address/preview", dependencies=[Depends(auth), Depends(csrf)])
    def address_preview(device_ids: list[str] | None = Body(None, embed=True)) -> dict[str, Any]:
        """Suggested address per device that is not in its block, with the values the call would use. Reads
        stored data only; the job checks everything again on the device."""
        policy_data, policy_errors = load_policy()
        if not policy_data:
            return {"devices": [], "errors": policy_errors or ["No policy"], "allow_address": cfg.allow_address}
        # Same suggestions as the findings of the overview: every device gets its own free address
        rep = report.build(store, policy_data, cfg.poll_interval_min)
        targets = {f["device_id"]: f.get("target") for f in rep["findings"] if f["kind"] == "network" and f["path"] == "ip"}
        rows = []
        for d in store.devices():
            blk = pol.address_block(policy_data, d)
            if d["id"] not in targets or not blk or (device_ids is not None and d["id"] not in device_ids):
                continue
            sug = {"target": targets[d["id"]], "block": blk.get("name") or str(blk["range"])}
            config = ((store.latest_snapshot(d["id"]) or {}).get("data") or {}).get("config")
            row = {"device_id": d["id"], "name": d.get("name"), "host": d["host"], "target": sug["target"],
                   "block": sug["block"], "model_name": model_name(d.get("model"), d.get("app"), d.get("gen")),
                   "error": None if sug["target"] else f"Block '{sug['block']}' is full."}
            if sug["target"]:
                try:
                    row.update(addressmod.plan_call(config, d.get("status"), d["host"],
                                                    policy_data.get("network"), sug["target"]))
                except addressmod.AddressError as exc:
                    row["error"] = str(exc)
            rows.append(row)
        return {"devices": rows, "errors": [], "allow_address": cfg.allow_address}

    @app.post("/api/address/apply", status_code=202,
              dependencies=[Depends(auth), Depends(csrf), Depends(address_allowed)])
    async def address_apply(items: list[dict[str, Any]] = Body(..., embed=True)) -> dict[str, Any]:
        """Starts the job: [{device_id, ip}], one device after the other. Status via /api/address/job."""
        try:
            job = collector.address.start(items)
        except KeyError as exc:
            raise HTTPException(404, f"Unknown device {exc.args[0]}") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"job": job.view()}

    @app.post("/api/address/job/cancel", dependencies=[Depends(auth), Depends(csrf)])
    def address_cancel() -> dict[str, Any]:
        return {"cancelled": collector.address.cancel()}

    # ---- Restart ------------------------------------------------------------------------

    def reboot_allowed() -> None:
        if not cfg.allow_reboot:
            raise HTTPException(403, "Restarting devices is disabled. Turn on 'Allow restarting devices' in Settings.")

    @app.post("/api/devices/{device_id}/reboot", dependencies=[Depends(auth), Depends(csrf), Depends(reboot_allowed)])
    async def reboot_device(device_id: str) -> dict[str, Any]:
        """Restarts the device and answers only once it is back (at most ~90 s)."""
        try:
            return {"device_id": device_id, **await collector.reboot(device_id)}
        except KeyError:
            raise HTTPException(404, "Unknown device") from None
        except RebootError as exc:
            raise HTTPException(502, str(exc)) from None

    # ---- Edit configuration -------------------------------------------------------------

    def config_allowed() -> None:
        if not cfg.allow_config:
            raise HTTPException(403, "Config changes are disabled. Turn on 'Allow config changes' in Settings.")

    @app.post("/api/devices/{device_id}/config/preview", dependencies=[Depends(auth), Depends(csrf)])
    def config_preview(device_id: str, changes: Any = Body(..., embed=True)) -> dict[str, Any]:
        """Preview: path, old, new and the exact SetConfig calls. Writes nothing, even without the switch."""
        try:
            plan = collector.config_plan(device_id, changes)
        except KeyError:
            raise HTTPException(404, "Unknown device") from None
        except ConfigInvalid as exc:
            return JSONResponse({"ok": False, "errors": exc.errors}, status_code=422)
        return {"ok": True, "allow_config": cfg.allow_config, **plan}

    @app.post("/api/devices/{device_id}/config", dependencies=[Depends(auth), Depends(csrf), Depends(config_allowed)])
    async def config_write(device_id: str, changes: Any = Body(..., embed=True)) -> dict[str, Any]:
        try:
            return {"device_id": device_id, **await collector.set_config(device_id, changes)}
        except KeyError:
            raise HTTPException(404, "Unknown device") from None
        except ConfigInvalid as exc:
            return JSONResponse({"ok": False, "errors": exc.errors}, status_code=422)
        except ConfigError as exc:
            return JSONResponse({"ok": False, "detail": str(exc), **exc.result}, status_code=502)

    # ---- Backups ------------------------------------------------------------------------------

    def _snap_or_404(snap_id: int) -> dict[str, Any]:
        snap = store.snapshot(snap_id)
        if not snap:
            raise HTTPException(404, "Unknown snapshot")
        return snap

    @app.get("/api/snapshots/{snap_id}/diff", dependencies=[Depends(auth)])
    def snapshot_diff(snap_id: int, against: int | None = None) -> dict[str, Any]:
        """Diff to the given snapshot or, without `against`, to the previous one of the same device."""
        new = _snap_or_404(snap_id)
        if against is None:
            older = [s for s in store.snapshots(new["device_id"]) if s["id"] < snap_id]
            if not older:
                return {"old": None, "new": snap_id, "changes": []}
            against = older[0]["id"]
        old = _snap_or_404(against)
        changes = compare.diff_flat(compare.backup_flat(old["data"]), compare.backup_flat(new["data"]))
        return {"old": against, "new": snap_id, "old_device": old["device_id"], "new_device": new["device_id"], "changes": changes}

    def _backup_doc(snap: dict[str, Any]) -> dict[str, Any]:
        return {
            "format": "relayroster-backup/1",
            "device_id": snap["device_id"],
            "taken_at": snap["taken_at"],
            "confirmed_at": snap["confirmed_at"],
            "firmware": snap.get("ver"),
            **snap["data"],
        }

    def _filename(snap: dict[str, Any]) -> str:
        dev = store.device(snap["device_id"]) or {}
        stamp = snap["taken_at"][:19].replace(":", "").replace("-", "")
        name = _SAFE_NAME.sub("_", (dev.get("name") or "").strip()) or "unnamed"
        dev_id = _SAFE_NAME.sub("_", snap["device_id"])   # reported by the device, so sanitise it as well
        return f"{dev_id}_{name}_{stamp}.json"

    @app.get("/api/snapshots/{snap_id}/download", dependencies=[Depends(auth)])
    def snapshot_download(snap_id: int) -> Response:
        snap = _snap_or_404(snap_id)
        body = json.dumps(_backup_doc(snap), indent=2, ensure_ascii=False)
        return Response(
            body, media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{_filename(snap)}"'},
        )

    @app.get("/api/backup.zip", dependencies=[Depends(auth)])
    def backup_all() -> StreamingResponse:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for dev in store.devices():
                snap = store.latest_snapshot(dev["id"])
                if snap:
                    zf.writestr(_filename(snap), json.dumps(_backup_doc(snap), indent=2, ensure_ascii=False))
            zf.writestr("policy.yaml", pol.read_text(cfg.policy_path))
        buf.seek(0)
        return StreamingResponse(
            buf, media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="relayroster-backup.zip"'},
        )

    # ---- Policy -------------------------------------------------------------------------------

    @app.get("/api/policy", dependencies=[Depends(auth)])
    def get_policy() -> dict[str, Any]:
        _, errors = load_policy()
        text = pol.read_text(cfg.policy_path)
        return {"text": text, "errors": errors, "hash": text_hash(text)}

    @app.post("/api/policy/validate", dependencies=[Depends(auth), Depends(csrf)])
    def validate_policy(text: str = Body(..., embed=True)) -> dict[str, Any]:
        try:
            pol.parse(text)
            return {"ok": True, "errors": []}
        except pol.PolicyError as exc:
            return {"ok": False, "errors": exc.errors}

    def text_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @app.put("/api/policy", dependencies=[Depends(auth), Depends(csrf)])
    def put_policy(text: str = Body(..., embed=True), base_hash: str | None = Body(None, embed=True)) -> JSONResponse:
        """base_hash: hash of the version the editor loaded. If the file differs by now, 409."""
        if len(text) > 256_000:
            raise HTTPException(413, "Policy too large")
        try:
            pol.parse(text)
        except pol.PolicyError as exc:
            return JSONResponse({"ok": False, "errors": exc.errors}, status_code=422)
        with policy_lock:
            if base_hash and text_hash(pol.read_text(cfg.policy_path)) != base_hash:
                return JSONResponse({"ok": False, "conflict": True, "errors": [
                    "policy.yaml was changed elsewhere since you opened it (another browser tab, the rule editor or Apply)."]},
                    status_code=409)
            pe.save_text(cfg.policy_path, text)   # with a backup of the old file
        store.add_audit("", "policy", None, None, True, "edited in the YAML editor")
        return JSONResponse({"ok": True, "errors": [], "hash": text_hash(text)})

    # ---- Profile ------------------------------------------------------------------------
    # Changes via ruamel.yaml round trip: comments and order in policy.yaml are kept.

    policy_lock = threading.Lock()

    def edit_policy(change) -> JSONResponse | None:
        """Applies change(doc) to policy.yaml. Errors as JSONResponse, success None."""
        with policy_lock:
            text = pol.read_text(cfg.policy_path)
            try:
                pol.parse(text)
            except pol.PolicyError:
                return JSONResponse({"ok": False, "errors": [
                    "policy.yaml is invalid. Fix it in the YAML tab first."]}, status_code=409)
            try:
                new_text, _ = pe.candidate(text, change)
                pe.save_text(cfg.policy_path, new_text)
                store.add_audit("", "policy", None, None, True, "changed by RelayRoster (rules, apply)")
            except pe.EditError as exc:
                return JSONResponse({"ok": False, "errors": exc.errors}, status_code=exc.status)
            except pol.PolicyError as exc:
                return JSONResponse({"ok": False, "errors": exc.errors}, status_code=422)
        return None

    def device_configs() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        devices = store.devices()
        return devices, report._latest_configs(store, devices)

    def templates() -> list[dict[str, Any]]:
        with open(pol.DEFAULT_POLICY_PATH, encoding="utf-8") as fh:
            return pe.list_profiles(pe.load_doc(fh.read()))

    @app.get("/api/profiles", dependencies=[Depends(auth)])
    def get_profiles() -> dict[str, Any]:
        text = pol.read_text(cfg.policy_path)
        policy_data, errors = load_policy()
        devices = store.devices()
        rep = report.build(store, policy_data, cfg.poll_interval_min) if policy_data else {"findings": []}
        stats = profiles_view.summary(policy_data, devices, rep["findings"])
        profiles = []
        baseline, overrides = None, []
        if not errors:
            doc = pe.load_doc(text)
            baseline = {"settings": pe.baseline_json(doc), **stats[profiles_view.BASELINE]}
            overrides = pe.list_overrides(doc)
            for p in pe.list_profiles(doc):
                # devices: devices the profile applies to; assigned/exclude: assignment in policy.yaml
                profiles.append({**p, "assigned": p["devices"],
                                 **stats.get(p["name"], {"devices": [], "findings": 0})})
        return {"profiles": profiles, "baseline": baseline, "overrides": overrides, "errors": errors, "templates": templates(),
                "choices": profiles_view.choices(devices),
                "devices": [profiles_view.device_row(d) for d in devices]}

    @app.post("/api/profiles/preview", dependencies=[Depends(auth), Depends(csrf)])
    def preview_profile(profile: dict[str, Any] = Body(...), original: str | None = Body(None)) -> JSONResponse:
        policy_data, errors = load_policy()
        if errors:
            return JSONResponse({"ok": False, "errors": ["policy.yaml is invalid. Fix it in the YAML tab first."]},
                                status_code=409)
        try:
            data, prof = pe.preview_policy(policy_data, profile, original)
        except pe.EditError as exc:
            return JSONResponse({"ok": False, "errors": exc.errors}, status_code=422)
        errs = pol.validate(data)
        if errs:
            return JSONResponse({"ok": False, "errors": errs}, status_code=422)
        devices, configs = device_configs()
        return JSONResponse({"ok": True, "errors": [], **profiles_view.preview(pol.normalize(data), prof, devices,
                                                                              configs)})

    @app.post("/api/baseline/preview", dependencies=[Depends(auth), Depends(csrf)])
    def preview_baseline(settings: list[Any] = Body(..., embed=True)) -> JSONResponse:
        policy_data, errors = load_policy()
        if errors:
            return JSONResponse({"ok": False, "errors": ["policy.yaml is invalid. Fix it in the YAML tab first."]},
                                status_code=409)
        try:
            data, prof = pe.preview_baseline(policy_data, settings)
        except pe.EditError as exc:
            return JSONResponse({"ok": False, "errors": exc.errors}, status_code=422)
        errs = pol.validate(data)
        if errs:
            return JSONResponse({"ok": False, "errors": errs}, status_code=422)
        devices, configs = device_configs()
        return JSONResponse({"ok": True, "errors": [], **profiles_view.preview(pol.normalize(data), prof, devices,
                                                                              configs, baseline=True)})

    @app.put("/api/baseline", dependencies=[Depends(auth), Depends(csrf)])
    def put_baseline(settings: list[Any] = Body(..., embed=True)) -> JSONResponse:
        return edit_policy(lambda doc: pe.set_baseline_rules(doc, settings)) or JSONResponse({"ok": True, "errors": []})

    @app.post("/api/profiles", dependencies=[Depends(auth), Depends(csrf)])
    def create_profile(profile: dict[str, Any] = Body(...), position: int | None = Body(None)) -> JSONResponse:
        err = edit_policy(lambda doc: pe.upsert_profile(doc, profile, None, position))
        return err or JSONResponse({"ok": True, "errors": [], "name": str(profile.get("name") or "").strip()})

    @app.post("/api/profiles/order", dependencies=[Depends(auth), Depends(csrf)])
    def order_profiles(names: list[str] = Body(..., embed=True)) -> JSONResponse:
        return edit_policy(lambda doc: pe.reorder_profiles(doc, names)) or JSONResponse({"ok": True, "errors": []})

    @app.put("/api/profiles/{name:path}", dependencies=[Depends(auth), Depends(csrf)])
    def update_profile(name: str, profile: dict[str, Any] = Body(..., embed=True)) -> JSONResponse:
        err = edit_policy(lambda doc: pe.upsert_profile(doc, profile, name))
        return err or JSONResponse({"ok": True, "errors": [], "name": str(profile.get("name") or "").strip()})

    @app.delete("/api/profiles/{name:path}", dependencies=[Depends(auth), Depends(csrf)])
    def delete_profile(name: str) -> JSONResponse:
        return edit_policy(lambda doc: pe.delete_profile(doc, name)) or JSONResponse({"ok": True, "errors": []})

    @app.post("/api/devices/{device_id}/override", dependencies=[Depends(auth), Depends(csrf)])
    def add_override(device_id: str, rule: dict[str, Any] = Body(..., embed=True)) -> JSONResponse:
        if not store.device(device_id):
            raise HTTPException(404, "Unknown device")
        _, errs = pe.check_rule(rule)
        if errs:
            return JSONResponse({"ok": False, "errors": errs}, status_code=422)
        err = edit_policy(lambda doc: pe.set_override(doc, device_id, rule.get("path"), pe.value_from_rule(rule),
                                                       rule.get("comment")))
        return err or JSONResponse({"ok": True, "errors": []})

    @app.post("/api/profiles/{name:path}/devices", dependencies=[Depends(auth), Depends(csrf)])
    def assign_profile(name: str, add: list[str] = Body([], embed=True),
                       remove: list[str] = Body([], embed=True)) -> JSONResponse:
        """Assign devices to a profile or take them out. Writes `devices`/`exclude` in policy.yaml."""
        policy_data, errors = load_policy()
        if errors:
            return JSONResponse({"ok": False, "errors": ["policy.yaml is invalid. Fix it in the YAML tab first."]},
                                status_code=409)
        unknown = [i for i in [*add, *remove] if not store.device(i)]
        if unknown:
            return JSONResponse({"ok": False, "errors": [f"Unknown device {i}" for i in unknown]}, status_code=404)
        prof = next((p for p in policy_data["profiles"] if p.get("name") == name), None)
        if prof is None:
            return JSONResponse({"ok": False, "errors": [f"Profile '{name}' does not exist"]}, status_code=404)
        match = prof.get("match") or {}
        by_match = {d["id"] for d in store.devices() if match and pol.profile_matches(match, d)}
        err = edit_policy(lambda doc: pe.assign_devices(doc, name, add, remove, by_match))
        return err or JSONResponse({"ok": True, "errors": []})

    @app.post("/api/policy/rule/remove", dependencies=[Depends(auth), Depends(csrf)])
    def remove_rule(source: str = Body(..., embed=True), path: str = Body(..., embed=True),
                    device_id: str | None = Body(None, embed=True)) -> JSONResponse:
        """Remove a rule from policy.yaml, e.g. an outdated one."""
        return edit_policy(lambda doc: pe.remove_rule(doc, source, path, device_id)) or JSONResponse(
            {"ok": True, "errors": []})

    # ---- Apply profile, bulk change -----------------------------------------------------

    @app.get("/api/bulk/paths", dependencies=[Depends(auth)])
    def bulk_paths() -> dict[str, Any]:
        devices, configs = device_configs()
        return {"paths": applymod.fleet_paths(devices, configs),
                "devices": [profiles_view.device_row(d) for d in devices], "allow_config": cfg.allow_config,
                "allow_reboot": cfg.allow_reboot}

    @app.post("/api/apply/preview", dependencies=[Depends(auth), Depends(csrf)])
    def apply_preview(mode: str = Body(..., embed=True), profile: str | None = Body(None, embed=True),
                      path: str | None = Body(None, embed=True), value: Any = Body(None, embed=True),
                      device_ids: list[str] | None = Body(None, embed=True),
                      snapshot_id: int | None = Body(None, embed=True)) -> JSONResponse:
        """Preview per device: rows with status and the exact SetConfig calls. Writes nothing."""
        try:
            if mode == "profile":
                policy_data, errors = load_policy()
                if errors:
                    return JSONResponse({"ok": False, "errors": ["policy.yaml is invalid. Fix it in the YAML tab "
                                                                 "first."]}, status_code=409)
                plans = applymod.plan_profile(collector, policy_data, str(profile or ""), device_ids)
            elif mode == "bulk":
                if not device_ids:
                    return JSONResponse({"ok": False, "errors": ["No devices selected"]}, status_code=422)
                if isinstance(value, (list, dict)):
                    return JSONResponse({"ok": False, "errors": ["Value must be a single value"]}, status_code=422)
                plans = applymod.plan_bulk(collector, str(path or ""), value, device_ids)
            elif mode == "restore":
                plans = applymod.plan_restore(collector, snapshot_id)
            else:
                return JSONResponse({"ok": False, "errors": [f"Unknown mode '{mode}'"]}, status_code=422)
        except KeyError as exc:
            return JSONResponse({"ok": False, "errors": [f"Unknown device {exc.args[0]}"]}, status_code=404)
        except LookupError as exc:
            return JSONResponse({"ok": False, "errors": [str(exc)]}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"ok": False, "errors": [str(exc)]}, status_code=422)
        return JSONResponse({"ok": True, "errors": [], "devices": plans, "allow_config": cfg.allow_config,
                             "allow_reboot": cfg.allow_reboot})

    @app.get("/api/apply/job", dependencies=[Depends(auth)])
    def apply_job() -> dict[str, Any]:
        job = collector.apply.job
        return {"job": job.view() if job else None}

    @app.post("/api/apply", status_code=202, dependencies=[Depends(auth), Depends(csrf), Depends(config_allowed)])
    async def apply_start(items: list[dict[str, Any]] = Body(..., embed=True), title: str = Body("", embed=True),
                          restart: bool = Body(False, embed=True),
                          policy: dict[str, Any] | None = Body(None, embed=True)) -> JSONResponse:
        """Starts the job. policy (optional): {scope: baseline|overrides, path, value, comment}: the rule goes
        into policy.yaml first, for 'overrides' for every device from items."""
        job = collector.apply.job
        if job and job.running:
            return JSONResponse({"ok": False, "errors": ["Settings are already being written"]}, status_code=409)
        if policy:
            scope, rpath = policy.get("scope"), str(policy.get("path") or "")
            comment = str(policy.get("comment") or "").strip()
            ids = [str(i.get("device_id")) for i in items if i.get("changes")]
            if scope == "baseline":
                err = edit_policy(lambda doc: pe.set_baseline(doc, rpath, policy.get("value"), comment))
            elif scope == "overrides":
                def change(doc):
                    for dev_id in ids:
                        pe.set_override(doc, dev_id, rpath, policy.get("value"), comment)
                err = edit_policy(change)
            else:
                return JSONResponse({"ok": False, "errors": [f"Unknown policy scope '{scope}'"]}, status_code=422)
            if err:
                return err
        try:
            job = collector.apply.start(items, title, restart)
        except KeyError as exc:
            return JSONResponse({"ok": False, "errors": [f"Unknown device {exc.args[0]}"]}, status_code=404)
        except ConfigInvalid as exc:
            return JSONResponse({"ok": False, "errors": [f"{k}: {v}" for k, v in exc.errors.items()]},
                                status_code=422)
        except ValueError as exc:
            return JSONResponse({"ok": False, "errors": [str(exc)]}, status_code=422)
        except RuntimeError as exc:
            return JSONResponse({"ok": False, "errors": [str(exc)]}, status_code=409)
        return JSONResponse({"ok": True, "job": job.view()}, status_code=202)

    @app.post("/api/restart", status_code=202, dependencies=[Depends(auth), Depends(csrf), Depends(reboot_allowed)])
    async def restart_devices(device_ids: list[str] = Body(..., embed=True)) -> JSONResponse:
        """Restart several devices one after the other. Runs as a job like bulk change (/api/apply/job)."""
        try:
            job = collector.apply.start_restart(device_ids)
        except KeyError as exc:
            return JSONResponse({"ok": False, "errors": [f"Unknown device {exc.args[0]}"]}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"ok": False, "errors": [str(exc)]}, status_code=422)
        except RuntimeError as exc:
            return JSONResponse({"ok": False, "errors": [str(exc)]}, status_code=409)
        return JSONResponse({"ok": True, "job": job.view()}, status_code=202)

    def set_password_allowed() -> None:
        if not cfg.allow_set_password:
            raise HTTPException(403, "Setting device passwords is disabled. Turn on 'Allow setting device passwords' "
                                     "in Settings.")

    @app.post("/api/auth/rollout", status_code=202,
              dependencies=[Depends(auth), Depends(csrf), Depends(set_password_allowed)])
    async def auth_rollout(device_ids: list[str] = Body(..., embed=True)) -> JSONResponse:
        """Set the device password from Settings on devices without password. Job like bulk change."""
        if not cfg.shelly_password:
            return JSONResponse({"ok": False, "errors": ["Set the device password in Settings first"]},
                                status_code=422)
        try:
            job = collector.apply.start_auth(device_ids)
        except KeyError as exc:
            return JSONResponse({"ok": False, "errors": [f"Unknown device {exc.args[0]}"]}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"ok": False, "errors": [str(exc)]}, status_code=422)
        except RuntimeError as exc:
            return JSONResponse({"ok": False, "errors": [str(exc)]}, status_code=409)
        return JSONResponse({"ok": True, "job": job.view()}, status_code=202)

    @app.post("/api/apply/job/cancel", dependencies=[Depends(auth), Depends(csrf)])
    def apply_cancel() -> dict[str, Any]:
        return {"cancelled": collector.apply.cancel()}

    # ---- Settings -----------------------------------------------------------------------

    @app.get("/api/settings", dependencies=[Depends(auth)])
    def get_settings() -> dict[str, Any]:
        return runtime.view()

    @app.put("/api/settings", dependencies=[Depends(auth), Depends(csrf)])
    def put_settings(settings: dict[str, Any] = Body(..., embed=True)) -> JSONResponse:
        try:
            changed = runtime.update(settings)
        except SettingsError as exc:
            return JSONResponse({"ok": False, "errors": exc.errors}, status_code=422)
        collector.settings_changed()
        if changed:   # only the names of the settings, never values (password)
            store.add_audit("", "settings", None, None, True, ", ".join(map(str, changed)))
        return JSONResponse({"ok": True, "changed": changed, **runtime.view()})

    # ---- Audit log across all devices ---------------------------------------------------

    def audit_rows(action: str, device_id: str | None, limit: int) -> list[dict[str, Any]]:
        names = {d["id"]: d.get("name") for d in store.devices()}
        return [{**e, "device_name": names.get(e["device_id"])}
                for e in store.audit(device_id, max(1, min(limit, 5000)), action or None)]

    @app.get("/api/audit", dependencies=[Depends(auth)])
    def audit_log(action: str = "", device_id: str | None = None, limit: int = 500) -> dict[str, Any]:
        return {"entries": audit_rows(action, device_id, limit), "actions": store.audit_actions()}

    @app.get("/api/audit.csv", dependencies=[Depends(auth)])
    def audit_csv(action: str = "", device_id: str | None = None, limit: int = 5000) -> Response:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["at", "device_id", "device_name", "action", "old", "new", "ok", "detail"])
        for e in audit_rows(action, device_id, limit):
            w.writerow([e["at"], e["device_id"], e["device_name"] or "", e["action"], e["old"] or "", e["new"] or "",
                        "ok" if e["ok"] else "failed", e["detail"] or ""])
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="relayroster-activity.csv"'})

    return app


app = create_app() if os.environ.get("RELAYROSTER_NO_AUTOAPP") != "1" else None
