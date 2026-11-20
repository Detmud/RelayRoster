"""HTTP API against the simulated fleet: overview, backups, policy editor, auth, CSRF.

Usage: python tests/test_api.py   (or pytest)
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import re
import tempfile
import zipfile

os.environ["RELAYROSTER_NO_AUTOAPP"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from fake_shelly import make_fleet  # noqa: E402
from test_core import make_settings  # noqa: E402

H = {"X-Requested-With": "relayroster"}



def all_js(c) -> str:
    """All UI scripts in the order from index.html (several files)."""
    html = c.get("/").text
    return "\n".join(c.get("/" + src).text for src in re.findall(r'<script src="(static/[^"?]+\.js)', html))

def setup(tmp: str, **kw):
    fleet = make_fleet()
    cfg = make_settings(tmp, shelly_password="secret-pw", **kw)
    app = create_app(cfg, transport=fleet.transport(), autostart=False)
    asyncio.run(app.state.collector.run(scan=True))
    return fleet, app, TestClient(app)


def test_overview_and_detail():
    with tempfile.TemporaryDirectory() as tmp:
        fleet, app, c = setup(tmp)
        r = c.get("/api/overview")
        assert r.status_code == 200
        body = r.json()
        assert len(body["devices"]) == 7
        assert body["policy_errors"] == []
        plus1 = next(d for d in body["devices"] if d["app"] == "Plus1")
        assert plus1["findings"] >= 2, "tz and wifi.ap from the default policy"
        cover = next(d for d in body["devices"] if d["id"] == "shelly2pmg4-58e6c5000202")
        assert "Shutter" in cover["profiles"]
        assert cover["model_name"] == "Shelly 2PM · Gen4" and cover["type_name"] == "Shelly 2PM" and cover["mac"] == "58:E6:C5:00:02:02"
        assert any(f["path"] == "cover:0.obstruction_detection.enable" and f["device_id"] == cover["id"]
                   for f in body["findings"])
        assert any(g["app"] == "S2PMG4" and g["diffs"] for g in body["peers"])
        assert any(g["model_name"] == "Shelly 2PM · Gen4" for g in body["peers"])
        fw = {g["app"]: g for g in body["firmware"]}
        assert fw["S2PMG4"]["model_name"] == "Shelly 2PM · Gen4"
        assert fw["S2PMG4"]["devices"][0]["mac"].count(":") == 5
        assert fw["PlusI4"]["uniform"] is False and fw["PlusI4"]["stable_available"] == "1.7.6"

        r = c.get(f"/api/devices/{cover['id']}")
        assert r.status_code == 200
        det = r.json()
        assert det["snapshot"]["data"]["config"]["cover:0"]["in_mode"] == "dual"
        assert any(x["source"] == "Profile: Shutter" for x in det["rules"])
        assert det["device"]["model_name"] == "Shelly 2PM · Gen4" and det["device"]["type_sub"] == "Gen4" and det["device"]["mac"] == "58:E6:C5:00:02:02"
        assert c.get("/api/devices/gibtsnicht").status_code == 404

        st = c.get("/api/state").json()
        assert st["read_only"] is True and st["last_summary"]["polled"] == 7


def test_backups():
    with tempfile.TemporaryDirectory() as tmp:
        fleet, app, c = setup(tmp)
        dev = c.get("/api/devices/shelly2pmg4-1020ba000201").json()
        sid = dev["snapshots"][0]["id"]
        r = c.get(f"/api/snapshots/{sid}/download")
        assert r.status_code == 200
        assert "attachment" in r.headers["content-disposition"]
        assert "Home_Office_Shutter" in r.headers["content-disposition"]
        doc = r.json()
        assert doc["format"] == "relayroster-backup/1" and doc["scripts"][0]["name"] == "frost_guard"

        fleet.devices["192.168.10.31"].config["cover:0"]["initial_state"] = "open"
        asyncio.run(app.state.collector.run())
        dev = c.get("/api/devices/shelly2pmg4-1020ba000201").json()
        assert len(dev["snapshots"]) == 2
        diff = c.get(f"/api/snapshots/{dev['snapshots'][0]['id']}/diff").json()
        assert diff["changes"] == [{"path": "config.cover:0.initial_state", "kind": "changed", "old": "stopped", "new": "open"}]
        first = c.get(f"/api/snapshots/{dev['snapshots'][1]['id']}/diff").json()
        assert first["old"] is None and first["changes"] == []

        r = c.get("/api/backup.zip")
        assert r.status_code == 200
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        assert "policy.yaml" in names and len(names) == 8


def test_policy_editor_and_csrf():
    with tempfile.TemporaryDirectory() as tmp:
        _, _, c = setup(tmp)
        text = c.get("/api/policy").json()["text"]
        assert "baseline:" in text

        assert c.put("/api/policy", json={"text": text}).status_code == 403, "blocked without header"
        assert c.post("/api/refresh").status_code == 403
        assert c.delete("/api/devices/shellyplus1-441793000301").status_code == 403

        r = c.put("/api/policy", json={"text": "baseline: [broken"}, headers=H)
        assert r.status_code == 422 and r.json()["errors"][0].startswith("YAML error")
        assert c.get("/api/policy").json()["text"] == text, "an invalid policy is not saved"

        r = c.post("/api/policy/validate", json={"text": "profiles: [{name: x}]"}, headers=H)
        assert r.json()["ok"] is False

        new = "baseline:\n  sys.location.tz: UTC\n"
        assert c.put("/api/policy", json={"text": new}, headers=H).status_code == 200
        body = c.get("/api/overview").json()
        tz = [f for f in body["findings"] if f["path"] == "sys.location.tz"]
        assert len(tz) == 6, "all except the Plus 1 (already on UTC) deviate now"

        assert c.delete("/api/devices/shellyplus1-441793000301", headers=H).status_code == 200
        assert len(c.get("/api/overview").json()["devices"]) == 6


def test_no_local_login():
    """No sign-in without OIDC; password login and basic auth no longer exist."""
    with tempfile.TemporaryDirectory() as tmp:
        _, _, c = setup(tmp)
        assert c.get("/login", follow_redirects=False).headers["location"] == "./"
        assert c.get("/", follow_redirects=False).status_code == 200
        st = c.get("/api/state").json()
        assert st["auth"] is False and st["user"] == "admin"
        assert c.post("/api/login", json={"user": "admin", "password": "pw"}, headers=H).status_code in (404, 405)
        assert c.get("/healthz").status_code == 200


def test_no_write_calls_through_api():
    with tempfile.TemporaryDirectory() as tmp:
        fleet, app, c = setup(tmp)
        c.get("/api/overview")
        c.post("/api/refresh?scan=true", headers=H)
        asyncio.run(app.state.collector.run(scan=True))
        from app.shelly import is_read_only
        bad = [m for m in fleet.all_calls() if not is_read_only(m)]
        assert not bad, bad
        assert json.dumps(fleet.all_calls())


def test_address_plan_in_overview():
    with tempfile.TemporaryDirectory() as tmp:
        _, _, c = setup(tmp)
        text = c.get("/api/policy").json()["text"]
        plan = ("\nnetwork:\n  subnet: 192.168.200.0/24\n  dhcp_pool: 192.168.10.0/24\n  blocks:\n"
                "    - {name: Plus i4, match: {id: 'shellyplusi4-*'}, range: 192.168.200.30-39}\n"
                "    - {name: 2PM Gen4, match: {id: 'shelly2pmg4-*'}, range: 192.168.200.100-129}\n")
        assert c.put("/api/policy", json={"text": text + plan}, headers=H).status_code == 200
        body = c.get("/api/overview").json()
        net = [f for f in body["findings"] if f["kind"] == "network"]
        i4 = sorted(f["note"] for f in net if f["device_id"].startswith("shellyplusi4-") and f["tag"] == "pool")
        assert len(i4) == 3
        # every new i4 gets its own address
        assert {n.split("block: ")[1].rstrip(".") for n in i4} == {"192.168.200.30", "192.168.200.31", "192.168.200.32"}
        rows = {d["id"]: d for d in body["devices"]}
        assert rows["shellyplusi4-b8d61a000101"]["address"]["tag"] == "pool"
        assert any(f["tag"] == "noblock" for f in net if f["device_id"] == "shellyplus1-441793000301")
        det = c.get("/api/devices/shelly2pmg4-1020ba000201").json()
        assert det["address_block"] == {"name": "2PM Gen4", "range": "192.168.200.100-129"}
        assert any(f["kind"] == "network" for f in det["findings"])
        # Detail and overview suggest the same address for every device
        for dev_id in rows:
            over = sorted(f["note"] for f in net if f["device_id"] == dev_id)
            detail = sorted(f["note"] for f in c.get(f"/api/devices/{dev_id}").json()["findings"] if f["kind"] == "network")
            assert over == detail, dev_id


def test_rename_disabled_by_default():
    with tempfile.TemporaryDirectory() as tmp:
        fleet, _, c = setup(tmp)
        st = c.get("/api/state").json()
        assert st["allow_config"] is False and st["read_only"] is True
        r = c.post("/api/devices/shellyplus1-441793000301/name", json={"name": "X"}, headers=H)
        assert r.status_code == 403 and "Settings" in r.json()["detail"]
        assert c.post("/api/names/apply", json={"device_ids": []}, headers=H).status_code == 403
        assert all(not w for d in fleet.devices.values() for w in d.writes)


def test_rename_and_naming_plan():
    with tempfile.TemporaryDirectory() as tmp:
        fleet, _, c = setup(tmp, allow_config=True)
        st = c.get("/api/state").json()
        assert st["allow_config"] is True and st["read_only"] is False
        url = "/api/devices/shellyplus1-441793000301/name"
        assert c.post(url, json={"name": "Carport Wall Switch"}).status_code == 403, "CSRF header required"
        assert c.post(url, json={"name": "a\u0007b"}, headers=H).status_code == 422
        assert c.post("/api/devices/gibtsnicht/name", json={"name": "X"}, headers=H).status_code == 404
        r = c.post(url, json={"name": "Carport Wall Switch"}, headers=H)
        assert r.status_code == 200 and r.json()["new"] == "Carport Wall Switch", r.text
        det = c.get("/api/devices/shellyplus1-441793000301").json()
        assert det["device"]["name"] == "Carport Wall Switch"
        assert det["audit"][0]["ok"] is True and det["audit"][0]["old"] == "Bedroom Wall Switch"

        # Device no longer answers: 502 with message, audit entry as failure
        del fleet.devices["192.168.10.32"]
        r = c.post("/api/devices/shelly2pmg4-58e6c5000202/name", json={"name": "Guest Room Shutter"}, headers=H)
        assert r.status_code == 502 and "ConnectError" in r.json()["detail"]

        # Namensplan
        text = c.get("/api/policy").json()["text"]
        plan = ("\nnames:\n  shellyplusi4-b8d61a000101: Living Room Wall Switch A\n"
                "  shellyplusi4-b8d61a000102: Wall Switch B\n"
                "  shelly2pmg4-1020ba000201: Home Office Shutter\n  shellyplus1-typo: Nope\n")
        assert c.put("/api/policy", json={"text": text + plan}, headers=H).status_code == 200
        body = c.get("/api/overview").json()
        assert body["names_unknown"] == ["shellyplus1-typo"]
        assert [f["device_id"] for f in body["findings"] if f["kind"] == "name"] == ["shellyplusi4-b8d61a000101"]
        changes = c.get("/api/names/plan").json()["changes"]
        assert [(x["device_id"], x["current"], x["planned"]) for x in changes] == [
            ("shellyplusi4-b8d61a000101", "Wall Switch A", "Living Room Wall Switch A")]
        r = c.post("/api/names/apply", json={"device_ids": ["shellyplusi4-b8d61a000101", "shellyplus1-441793000301"]},
                   headers=H).json()
        assert r["ok"] == 1 and r["failed"] == 1, r
        assert r["results"][1]["error"] == "Not part of the current naming plan", "only planned devices are written"
        assert fleet.devices["192.168.10.21"].config["sys"]["device"]["name"] == "Living Room Wall Switch A"
        assert c.get("/api/names/plan").json()["changes"] == []
        assert not [f for f in c.get("/api/overview").json()["findings"] if f["kind"] == "name"]

        from app.shelly import is_read_only, is_rename_call
        assert all(is_read_only(m) or m == "Sys.SetConfig" for m in fleet.all_calls())
        writes = [w for d in fleet.devices.values() for w in d.writes]
        assert len(writes) == 2 and all(is_rename_call("Sys.SetConfig", w) for w in writes)


def test_scan_state_warnings_and_cancel():
    with tempfile.TemporaryDirectory() as tmp:
        fleet, app, c = setup(tmp, scan_networks=["192.168.0.0/16", "10.0.0.0/8"])
        st = c.get("/api/state").json()
        assert st["scan_addresses"] == 65534 and st["scan_targets"] == ["192.168.0.0/16"]
        assert len(st["scan_warnings"]) == 1 and "10.0.0.0/8" in st["scan_warnings"][0]
        assert st["running"] is False and st["eta_s"] is None and st["auto_scan"] is True
        assert c.post("/api/scan/cancel").status_code == 403, "CSRF-Header Pflicht"
        assert c.post("/api/scan/cancel", headers=H).json() == {"cancelled": False}, "no scan active"

        # Cancel in the middle of a /16 scan: status shows progress, finds stay in the database
        col = app.state.collector
        snapshots = []

        class Yielding(httpx.AsyncBaseTransport):
            """Like a real socket, hands control back to the event loop on every request."""
            def __init__(self, inner):
                self.inner = inner

            async def handle_async_request(self, request):
                await asyncio.sleep(0)
                return await self.inner.handle_async_request(request)

        col.transport = Yielding(col.transport)

        async def go():
            task = asyncio.create_task(col.run(scan=True))
            while col.state.phase != "scan" or col.state.done < 3000:
                await asyncio.sleep(0)
            snapshots.append(c.get("/api/state").json())
            assert col.cancel_scan() is True
            return await task

        summary = asyncio.run(go())
        mid = snapshots[0]
        assert mid["running"] and mid["phase"] == "scan" and mid["total"] == 65534 and 3000 <= mid["done"] < 65534
        assert summary["cancelled"] is True and summary["scanned"] is True
        assert c.get("/api/state").json()["last_summary"]["cancelled"] is True
        assert len(c.get("/api/overview").json()["devices"]) == 7


def test_settings_in_app():
    """Settings in the UI, stored under /data, effective immediately, environment = default."""
    with tempfile.TemporaryDirectory() as tmp:
        fleet, app, c = setup(tmp)
        body = c.get("/api/settings").json()
        f = {x["key"]: x for x in body["fields"]}
        assert set(f) >= {"scan_networks", "poll_interval_min", "allow_config", "shelly_password"}
        assert f["shelly_password"]["value"] is None and f["shelly_password"]["is_set"] is True
        assert "secret-pw" not in json.dumps(body), "the password never leaves the server"
        assert f["allow_config"]["value"] is False and f["allow_config"]["overridden"] is False
        assert "UI_PASSWORD" not in body["env_only"] and "DATA_DIR" in body["env_only"]

        assert c.put("/api/settings", json={"settings": {"allow_config": True}}).status_code == 403, "CSRF"

        # Invalid input is rejected completely, none of it applies
        r = c.put("/api/settings", headers=H, json={"settings": {
            "allow_config": True, "poll_interval_min": 0, "scan_networks": ["10.0.0.0/8"],
            "extra_hosts": ["bad host!"], "nope": 1, "scan_timeout_s": "x"}})
        assert r.status_code == 422
        errs = r.json()["errors"]
        assert set(errs) == {"poll_interval_min", "scan_networks", "extra_hosts", "nope", "scan_timeout_s"}
        assert "/16" in errs["scan_networks"] and "1 to 1440" in errs["poll_interval_min"]
        assert c.get("/api/state").json()["allow_config"] is False

        # Enabling renaming takes effect immediately, without restart
        r = c.put("/api/settings", headers=H, json={"settings": {
            "allow_config": True, "poll_interval_min": 30.0,
            "scan_networks": "192.168.10.0/24, 192.168.20.7/24", "shelly_password": "new-pw"}})
        assert r.status_code == 200, r.text
        out = r.json()
        assert set(out["changed"]) == {"allow_config", "poll_interval_min", "scan_networks", "shelly_password"}
        assert "new-pw" not in json.dumps(out)
        st = c.get("/api/state").json()
        assert st["allow_config"] is True and st["read_only"] is False and st["poll_interval_min"] == 30
        assert st["scan_targets"] == ["192.168.10.0/24", "192.168.20.0/24"]
        assert app.state.settings.shelly_password == "new-pw"
        r = c.post("/api/devices/shellyplus1-441793000301/name", json={"name": "Bedroom Light"}, headers=H)
        assert r.status_code == 200, r.text
        assert fleet.devices["192.168.10.41"].writes

        # A value equal to the default is no override; null resets
        r = c.put("/api/settings", headers=H, json={"settings": {"poll_interval_min": 15, "allow_config": None}})
        f = {x["key"]: x for x in r.json()["fields"]}
        assert f["poll_interval_min"]["overridden"] is False and f["allow_config"]["value"] is False
        assert f["scan_networks"]["overridden"] is True

        # Overrides survive a restart (same /data, fresh environment)
        app2 = create_app(make_settings(tmp, shelly_password="secret-pw"), transport=fleet.transport(), autostart=False)
        f2 = {x["key"]: x for x in TestClient(app2).get("/api/settings").json()["fields"]}
        assert f2["scan_networks"]["value"] == ["192.168.10.0/24", "192.168.20.0/24"]
        assert f2["scan_networks"]["default"] == ["192.168.10.0/24"]
        assert app2.state.settings.shelly_password == "new-pw" and app2.state.settings.allow_config is False
        assert app2.state.collector.plan.total == 508


def test_interval_change_wakes_loop():
    """A new sync interval applies immediately, not only after the old one has passed."""
    import app.collector as colmod
    with tempfile.TemporaryDirectory() as tmp:
        _, app, _ = setup(tmp)
        col = app.state.collector
        clock = {"t": 0.0}
        real = colmod._clock
        colmod._clock = lambda: clock["t"]
        try:
            async def go():
                task = asyncio.create_task(col._wait_interval())
                await asyncio.sleep(0.05)
                assert not task.done(), "waits for the 15-minute interval"
                clock["t"] = 120.0                      # 2 min vergangen
                app.state.settings.poll_interval_min = 1
                col.settings_changed()
                await asyncio.wait_for(task, 1)
            asyncio.run(go())
        finally:
            colmod._clock = real


ADDRESS_PLAN = ("\nnetwork:\n  subnet: 192.168.200.0/24\n  dhcp_pool: 192.168.10.0/24\n"
                "  gateway: 192.168.200.1\n  nameserver: 192.168.200.1\n  blocks:\n"
                "    - {name: i4, match: {kind: i4}, range: 192.168.200.30-39}\n")


def test_address_change_disabled_by_default():
    with tempfile.TemporaryDirectory() as tmp:
        fleet, _, c = setup(tmp)
        r = c.post("/api/address/apply", json={"items": [{"device_id": "shellyplusi4-b8d61a000101",
                                                           "ip": "192.168.200.30"}]}, headers=H)
        assert r.status_code == 403 and "Settings" in r.json()["detail"]
        assert c.get("/api/state").json()["allow_address"] is False
        assert not any(d.config_writes for d in fleet.devices.values())


def test_address_change_job():
    """Static address from the address plan: preview, move, device that does not come back, address in use."""
    import time as _time
    from app import address
    from app.shelly import is_address_call
    sta = {"ipv4mode": "static", "ip": "10.0.0.5", "netmask": "255.255.255.0", "gw": "10.0.0.1", "nameserver": None}
    assert is_address_call("WiFi.SetConfig", {"config": {"sta": sta}}) and is_address_call("Eth.SetConfig", {"config": sta})
    assert not is_address_call("WiFi.SetConfig", {"config": {"sta": {**sta, "ssid": "Other"}}}), "address fields only"
    assert not is_address_call("WiFi.SetConfig", {"config": {"sta": {**sta, "ipv4mode": "dhcp"}}})
    assert not is_address_call("WiFi.SetConfig", {"config": {"sta": {**sta, "ip": "10.0.0.999"}}})
    assert not is_address_call("WiFi.SetConfig", {"config": {"sta1": sta}}) and not is_address_call("Sys.SetConfig", {"config": sta})
    # Netmask, gateway and DNS server only from the policy, also when the device has static values of its own
    net = {"subnet": "10.0.0.0/24", "dhcp_pool": "10.0.0.100/27", "gateway": "10.0.0.1", "nameserver": "10.0.0.2"}
    st = {"wifi": {"sta_ip": "10.0.0.5", "ssid": "IoT"}}
    blk = {"name": "i4", "range": "10.0.0.30-39"}
    own = address.plan_call({"wifi": {"sta": {**sta, "ssid": "IoT", "gw": "10.0.0.254"}}}, st, "10.0.0.5", net, "10.0.0.31", blk)
    assert (own["method"], own["netmask"], own["gw"], own["nameserver"], own["source"]) == (
        "WiFi.SetConfig", "255.255.255.0", "10.0.0.1", "10.0.0.2", "policy")
    bad = [((None, "10.0.0.9"), "network.subnet, network.dhcp_pool, network.gateway, network.nameserver"),
           (({**net, "dhcp_pool": None}, "10.0.0.9"), "network.dhcp_pool"),
           ((net, "10.9.9.9"), "not in network.subnet"),
           (({**net, "gateway": "10.0.1.1"}, "10.0.0.9"), "not in 10.0.0.0/24"),
           ((net, "10.0.0.0"), "network address"), ((net, "10.0.0.255"), "broadcast address"),
           ((net, "10.0.0.1"), "is the gateway"), ((net, "10.0.0.2"), "is the DNS server"),
           ((net, "10.0.0.101"), "DHCP pool"), ((net, "nonsense"), "not an IPv4 address")]
    for (network, target), word in bad:
        try:
            address.plan_call({}, st, "10.0.0.5", network, target)
            raise AssertionError(word)
        except address.AddressError as exc:
            assert word in str(exc), exc
    for args, word in ((({}, {"wifi": {"sta_ip": "172.16.0.5"}}, "10.0.0.5", net, "10.0.0.9"), "reports 172.16.0.5"),
                       (({}, st, "10.0.0.5", net, "10.0.0.9", blk), "not in block 'i4'")):
        try:
            address.plan_call(*args)
            raise AssertionError(word)
        except address.AddressError as exc:
            assert word in str(exc), exc

    with tempfile.TemporaryDirectory() as tmp:
        fleet, app, _ = setup(tmp, allow_address=True)
        mover = app.state.collector.address
        mover.poll_s, mover.timeout_s, mover.probe_s = 0.01, 0.3, 0.2
        a, b, cdev = "shellyplusi4-b8d61a000101", "shellyplusi4-b8d61a000102", "shellyplusi4-b8d61a000103"
        fleet.devices["192.168.10.22"].address_lost = True          # takes the address, never shows up there
        fleet.devices["192.168.200.32"] = fleet.devices["192.168.10.41"]   # something answers at this address
        with TestClient(app) as c:
            text = c.get("/api/policy").json()["text"]
            assert c.put("/api/policy", json={"text": text + ADDRESS_PLAN}, headers=H).status_code == 200
            pv = {d["device_id"]: d for d in c.post("/api/address/preview", json={}, headers=H).json()["devices"]}
            assert {d["target"] for d in pv.values()} == {"192.168.200.30", "192.168.200.31", "192.168.200.32"}
            assert pv[a]["error"] is None and (pv[a]["netmask"], pv[a]["gw"], pv[a]["source"]) == (
                "255.255.255.0", "192.168.200.1", "policy")
            # Asked for by ID: listed whatever its block, suggested address is its current one; a typed address
            # is checked instead, against the same rules as the job
            typed = c.post("/api/address/preview", json={"device_ids": [a], "targets": {a: "192.168.200.40"}},
                           headers=H).json()["devices"][0]
            assert typed["target"] == "192.168.200.40" and "not in block 'i4'" in typed["error"], typed
            one = c.post("/api/address/preview", json={"device_ids": ["shellyplus1-441793000301"]}, headers=H).json()
            assert [(d["device_id"], d["target"], d["block"]) for d in one["devices"]] == [
                ("shellyplus1-441793000301", "192.168.10.41", None)] and "not in network.subnet" in one["devices"][0]["error"]
            items = [{"device_id": a, "ip": "192.168.200.30"}, {"device_id": b, "ip": "192.168.200.31"},
                     {"device_id": cdev, "ip": "192.168.200.32"}]
            assert c.post("/api/address/apply", json={"items": items}).status_code == 403, "CSRF"
            assert c.post("/api/address/apply", json={"items": [{"device_id": "nope", "ip": "192.168.200.30"}]},
                          headers=H).status_code == 404
            r = c.post("/api/address/apply", json={"items": [{"device_id": a, "ip": "192.168.10.41"}]}, headers=H)
            assert r.status_code == 422 and "address of shellyplus1" in r.json()["detail"], "address of a known device"
            assert c.post("/api/address/apply", json={"items": [{"device_id": a, "ip": "nonsense"}]},
                          headers=H).status_code == 422
            assert c.post("/api/address/apply", json={"items": items}, headers=H).status_code == 202
            for _ in range(500):
                job = c.get("/api/address/job").json()["job"]
                if not job["running"]:
                    break
                _time.sleep(0.01)
            rows = {d["id"]: d for d in c.get("/api/overview").json()["devices"]}
            audit = c.get("/api/audit?action=address").json()["entries"]
            # Keeping the current address: no probe (the device itself answers there), set static in place
            fleet.devices["192.168.200.30"].config_writes.clear()
            assert c.post("/api/address/apply", json={"items": [{"device_id": a, "ip": "192.168.200.30"}]},
                          headers=H).status_code == 202
            for _ in range(500):
                again = c.get("/api/address/job").json()["job"]
                if not again["running"]:
                    break
                _time.sleep(0.01)
        assert again["items"][0]["status"] == "done", again
        assert fleet.devices["192.168.200.30"].config_writes[-1][1]["config"]["sta"]["ip"] == "192.168.200.30"
        res = {it["device_id"]: it for it in job["items"]}
        assert res[a]["status"] == "done" and (res[a]["old"], res[a]["new"]) == ("192.168.10.21", "192.168.200.30"), res[a]
        moved = fleet.devices["192.168.200.30"]
        assert moved.id == a and "192.168.10.21" not in fleet.devices
        assert moved.config_writes[-1] == ("WiFi.SetConfig", {"config": {"sta": {
            "ipv4mode": "static", "ip": "192.168.200.30", "netmask": "255.255.255.0", "gw": "192.168.200.1",
            "nameserver": "192.168.200.1"}}})
        assert rows[a]["host"] == "192.168.200.30" and rows[a]["address"] is None, "stored under the same ID, plan fulfilled"
        assert res[b]["status"] == "failed" and "192.168.200.31" in res[b]["error"] and "192.168.10.22" in res[b]["error"]
        assert rows[b]["host"] == "192.168.10.22", "the old address stays until the device is seen at the new one"
        assert res[cdev]["status"] == "failed" and "in use" in res[cdev]["error"]
        assert not fleet.devices["192.168.10.23"].config_writes, "nothing written when the address is in use"
        assert [(e["device_id"], e["ok"]) for e in audit] == [(cdev, False), (b, False), (a, True)]


def test_firmware_update_disabled_by_default():
    with tempfile.TemporaryDirectory() as tmp:
        fleet, _, c = setup(tmp)
        r = c.post("/api/firmware/update", json={"device_ids": ["shellyplusi4-b8d61a000101"]}, headers=H)
        assert r.status_code == 403 and "Settings" in r.json()["detail"]
        assert c.get("/api/state").json()["allow_firmware_update"] is False
        assert not any(d.updates for d in fleet.devices.values())
        assert "Shelly.CheckForUpdate" not in fleet.all_calls()


def test_firmware_update_job():
    """Update one after the other, connection drop at the start, restart, check, failure, cancel."""
    import time as _time
    with tempfile.TemporaryDirectory() as tmp:
        fleet, app, _ = setup(tmp, allow_firmware_update=True)
        fw = app.state.collector.firmware
        fw.poll_s, fw.timeout_s = 0.01, 0.5
        i4a = fleet.devices["192.168.10.21"]      # 1.7.5, stable 1.7.6 offered
        i4a.drop_on_update = True                 # connection drops on Shelly.Update
        plus1 = fleet.devices["192.168.10.41"]
        plus1.stable, plus1.update_fails = "1.7.6", True   # restarts but stays on 1.7.5
        ids = ["shellyplusi4-b8d61a000101", "shellyplusi4-b8d61a000102", "shellyplus1-441793000301"]

        with TestClient(app) as c:
            assert c.post("/api/firmware/update", json={"device_ids": ids}).status_code == 403, "CSRF"
            assert c.post("/api/firmware/update", json={"device_ids": ids, "stage": "nightly"},
                          headers=H).status_code == 422
            assert c.post("/api/firmware/update", json={"device_ids": ["gibtsnicht"]}, headers=H).status_code == 404
            r = c.post("/api/firmware/update", json={"device_ids": ids}, headers=H)
            assert r.status_code == 202, r.text
            assert c.post("/api/firmware/update", json={"device_ids": ids}, headers=H).status_code == 409
            for _ in range(500):
                job = c.get("/api/firmware/job").json()["job"]
                if not job["running"]:
                    break
                _time.sleep(0.01)
            st = c.get("/api/state").json()

        items = {it["device_id"]: it for it in job["items"]}
        a = items["shellyplusi4-b8d61a000101"]
        assert a["status"] == "done" and a["old"] == "1.7.5" and a["new"] == "1.7.6", a
        # Every device reports the steps it went through; several devices run at the same time
        assert [p[0] for p in a["phases"]] == ["checking", "requested", "rebooting", "verifying", "done"], a["phases"]
        assert job["parallel"] == 4 and job["active"] == 0 and st["firmware_parallel"] == 4
        assert items["shellyplusi4-b8d61a000102"]["status"] == "up_to_date", "already on 1.7.6, no update"
        p = items["shellyplus1-441793000301"]
        assert p["status"] == "failed" and "1.7.5" in p["error"], p
        assert job["counts"] == {"done": 1, "up_to_date": 1, "failed": 1}
        assert st["firmware_job"]["running"] is False and st["allow_firmware_update"] is True

        assert i4a.updates == ["stable"] and fleet.devices["192.168.10.22"].updates == []
        assert plus1.updates == ["stable"]
        from app.shelly import is_firmware_call, is_read_only
        assert all(is_read_only(m) or is_firmware_call(m, None) or m == "Shelly.Update" for m in fleet.all_calls())

        store = app.state.store
        assert store.device("shellyplusi4-b8d61a000101")["ver"] == "1.7.6", "read again after the update"
        audit = store.audit("shellyplusi4-b8d61a000101")
        assert audit[0]["action"] == "firmware" and audit[0]["ok"] and audit[0]["new"] == "1.7.6"
        assert store.audit("shellyplus1-441793000301")[0]["ok"] is False

        # Cancel: the first device finishes, the others do not start (one at a time: firmware_parallel = 1)
        app.state.collector.settings.firmware_parallel = 1
        plus1.update_fails, plus1.reboot_left = False, 0
        fleet.devices["192.168.10.22"].stable = "1.7.7"

        async def go():
            job = fw.start(["shellyplusi4-b8d61a000102", "shellyplus1-441793000301"])
            while not job.view()["active"]:      # the first device has started
                await asyncio.sleep(0)
            assert fw.cancel() is True
            await fw.wait()
            return job.view()
        v = asyncio.run(go())
        assert [it["status"] for it in v["items"]] == ["done", "skipped"] and v["cancelled"] is True
        assert v["parallel"] == 1

        # Several at a time: both devices are in progress together, a cancel lets the running ones finish
        app.state.collector.settings.firmware_parallel = 4
        fleet.devices["192.168.10.22"].stable, plus1.stable = "1.7.8", "1.7.8"

        async def together():
            job = fw.start(["shellyplusi4-b8d61a000102", "shellyplus1-441793000301"])
            most = 0
            while job.running:
                most = max(most, job.view()["active"])
                await asyncio.sleep(0.001)
            return most, job.view()
        most, v = asyncio.run(together())
        assert most == 2 and [it["status"] for it in v["items"]] == ["done", "done"], (most, v["items"])


def test_reboot_disabled_by_default():
    """No restart without the switch, no Shelly.Reboot on the network."""
    with tempfile.TemporaryDirectory() as tmp:
        fleet, _, c = setup(tmp)
        st = c.get("/api/state").json()
        assert st["allow_reboot"] is False and st["read_only"] is True
        r = c.post("/api/devices/shellyplus1-441793000301/reboot", headers=H)
        assert r.status_code == 403 and "Settings" in r.json()["detail"]
        assert "Shelly.Reboot" not in fleet.all_calls()


def test_reboot_device():
    """Check identity, Shelly.Reboot without parameters, confirm the restart via uptime, audit log."""
    with tempfile.TemporaryDirectory() as tmp:
        fleet, app, c = setup(tmp, allow_reboot=True)
        col = app.state.collector
        col.reboot_poll_s, col.reboot_timeout_s = 0.01, 0.5
        assert c.get("/api/state").json()["read_only"] is False
        url = "/api/devices/shellyplus1-441793000301/reboot"
        assert c.post(url).status_code == 403, "CSRF header required"
        assert c.post("/api/devices/gibtsnicht/reboot", headers=H).status_code == 404

        r = c.post(url, headers=H)
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        plus1 = fleet.devices["192.168.10.41"]
        assert plus1.reboots == [{}], "Shelly.Reboot exactly once, without parameters"
        det = c.get("/api/devices/shellyplus1-441793000301").json()
        assert det["audit"][0]["action"] == "reboot" and det["audit"][0]["ok"] is True
        assert det["device"]["status"]["sys"]["uptime"] < 12345, "read again after the restart"
        # Second restart of the same device: confirmed via the lower uptime again
        assert c.post(url, headers=H).status_code == 200 and len(plus1.reboots) == 2

        # Device accepts the command but does not restart: error with hint, audit entry as failure
        g4 = fleet.devices["192.168.10.31"]
        g4.reboot_ignored = True
        r = c.post("/api/devices/shelly2pmg4-1020ba000201/reboot", headers=H)
        assert r.status_code == 502 and "did not confirm a restart" in r.json()["detail"]
        assert c.get("/api/devices/shelly2pmg4-1020ba000201").json()["audit"][0]["ok"] is False

        # A different device answers at the address: no restart
        fleet.devices["192.168.10.23"] = fleet.devices.pop("192.168.10.22")
        fleet.devices["192.168.10.22"] = fleet.devices["192.168.10.21"]
        r = c.post("/api/devices/shellyplusi4-b8d61a000102/reboot", headers=H)
        assert r.status_code == 502 and "answers as" in r.json()["detail"]
        assert fleet.devices["192.168.10.21"].reboots == []

        from app.shelly import is_firmware_call, is_read_only, is_reboot_call, is_rename_call
        assert all(is_read_only(m) or m == "Shelly.Reboot" for m in fleet.all_calls())
        assert not is_reboot_call("Shelly.Reboot", {"delay_ms": 1000})
        assert is_reboot_call("Shelly.Reboot", None) and not is_rename_call("Shelly.Reboot", None)
        assert not is_firmware_call("Shelly.Reboot", None)


def test_config_disabled_by_default():
    """No *.SetConfig on the network without the switch; the preview still works and writes nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        fleet, _, c = setup(tmp)
        st = c.get("/api/state").json()
        assert st["allow_config"] is False and st["read_only"] is True
        url = "/api/devices/shelly2pmg4-1020ba000201/config"
        ch = {"changes": {"cover:0.power_limit": 280}}
        r = c.post(url, headers=H, json=ch)
        assert r.status_code == 403 and "Settings" in r.json()["detail"]
        r = c.post(url + "/preview", headers=H, json=ch)
        assert r.status_code == 200 and r.json()["allow_config"] is False
        assert all(not d.config_writes for d in fleet.devices.values())
        assert all(not m.endswith("SetConfig") for m in fleet.all_calls())


def test_config_write():
    """The preview shows exactly the calls, writing only changed fields, read-back, snapshot, audit per path."""
    from app.shelly import is_config_call, is_read_only
    with tempfile.TemporaryDirectory() as tmp:
        fleet, app, c = setup(tmp, allow_config=True)
        dev_id = "shelly2pmg4-1020ba000201"
        g4 = fleet.devices["192.168.10.31"]
        url = f"/api/devices/{dev_id}/config"
        assert c.get("/api/state").json()["read_only"] is False
        det = c.get(f"/api/devices/{dev_id}").json()
        fields = {f["path"]: f for s in det["settings"]["sections"] for f in s["fields"]}
        assert fields["cover:0.power_limit"]["editable"] and fields["sys.device.name"]["editable"], "name is editable"
        assert not any(f["path"] == "cover:0.maxtime_open" and f.get("editable") for f in det["settings"]["info"])
        snaps_before = len(det["snapshots"])

        changes = {"cover:0.power_limit": 280, "cover:0.obstruction_detection.direction": "close",
                   "cover:0.invert_directions": False,       # unchanged: drops out
                   "sys.location.tz": "UTC"}
        assert c.post(url + "/preview", json={"changes": changes}).status_code == 403, "CSRF"
        r = c.post(url + "/preview", headers=H, json={"changes": changes})
        assert r.status_code == 200, r.text
        prev = r.json()
        assert [x["path"] for x in prev["rows"]] == ["cover:0.obstruction_detection.direction", "cover:0.power_limit",
                                                     "sys.location.tz"]
        assert prev["rows"][1]["old"] == 300 and prev["rows"][1]["new"] == 280 and prev["rows"][1]["unit"] == "W"
        assert [(x["method"], x["params"]) for x in prev["calls"]] == [
            ("Cover.SetConfig", {"id": 0, "config": {"obstruction_detection": {"direction": "close"}, "power_limit": 280}}),
            ("Sys.SetConfig", {"config": {"location": {"tz": "UTC"}}})]
        assert not g4.config_writes, "the preview writes nothing"

        # Invalid: error per path, nothing on the network
        bad = {"cover:0.power_limit": "viel", "cover:0.maxtime_open": 10, "wifi.sta.ssid": "x",
               "cover:0.in_mode": "triple", "cover:3.power_limit": 1, "sys.device.name": " "}
        r = c.post(url, headers=H, json={"changes": bad})
        assert r.status_code == 422 and set(r.json()["errors"]) == set(bad), r.text
        assert c.post(url, headers=H, json={"changes": {}}).status_code == 422
        assert c.post("/api/devices/gibtsnicht/config", headers=H, json={"changes": changes}).status_code == 404
        assert not g4.config_writes

        r = c.post(url, headers=H, json={"changes": changes})
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        assert [(m, p) for m, p in g4.config_writes] == [(x["method"], x["params"]) for x in prev["calls"]], \
            "exactly what the preview shows is sent"
        assert all(is_config_call(m, p) for m, p in g4.config_writes)
        assert g4.config["cover:0"]["power_limit"] == 280 and g4.config["sys"]["location"]["tz"] == "UTC"
        det = c.get(f"/api/devices/{dev_id}").json()
        assert len(det["snapshots"]) == snaps_before + 1, "new snapshot after writing"
        audit = [a for a in det["audit"] if a["action"] == "config"]
        assert sorted(a["detail"] for a in audit) == sorted(x["path"] for x in prev["rows"])
        pl = next(a for a in audit if a["detail"] == "cover:0.power_limit")
        assert pl["old"] == "300" and pl["new"] == "280" and pl["ok"] is True
        # The same again: nothing to do, no call
        n = len(g4.config_writes)
        assert c.post(url, headers=H, json={"changes": changes}).json()["changed"] is False
        assert len(g4.config_writes) == n

        # Restart required (firmware reports restart_required)
        r = c.post(url, headers=H, json={"changes": {"cover:0.invert_directions": True}})
        assert r.status_code == 200 and r.json()["restart_required"] is True
        assert "restart required" in c.get(f"/api/devices/{dev_id}").json()["audit"][0]["detail"]

        # Changed on the device in the meantime: abort without writing
        g4.config["cover:0"]["power_limit"] = 250
        r = c.post(url, headers=H, json={"changes": {"cover:0.power_limit": 260}})
        assert r.status_code == 502 and "Changed on the device" in r.json()["detail"]
        assert len(g4.config_writes) == n + 1

        # Shutter moving: the device refuses, the message comes through, the following component is not sent
        asyncio.run(app.state.collector.run())
        g4.config_error = "Cover is moving"
        r = c.post(url, headers=H, json={"changes": {"cover:0.power_limit": 260, "sys.location.tz": "Europe/Berlin"}})
        body = r.json()
        assert r.status_code == 502 and "Cover is moving" in body["detail"], body
        res = {x["path"]: x for x in body["results"]}
        assert res["cover:0.power_limit"]["ok"] is False and "not sent" in res["sys.location.tz"]["error"]
        assert [m for m, _ in g4.config_writes[n + 1:]] == ["Cover.SetConfig"]
        assert c.get(f"/api/devices/{dev_id}").json()["audit"][0]["ok"] is False

        # Device does not take a value: read-back reports the error
        g4.config_error = None
        g4.config_ignore = {"cover:0.power_limit"}
        r = c.post(url, headers=H, json={"changes": {"cover:0.power_limit": 260}})
        assert r.status_code == 502 and "reads back 250" in r.json()["detail"]

        # A different device answers at the address: no writing
        i4 = fleet.devices["192.168.10.22"]
        fleet.devices["192.168.10.22"] = fleet.devices["192.168.10.21"]
        r = c.post("/api/devices/shellyplusi4-b8d61a000102/config", headers=H,
                   json={"changes": {"input:0.type": "switch"}})
        assert r.status_code == 502 and "answers as" in r.json()["detail"]
        assert not fleet.devices["192.168.10.21"].config_writes and not i4.config_writes
        assert all(is_read_only(m) or m.endswith(".SetConfig") for m in fleet.all_calls())


def test_static_assets_revalidate():
    """New version = new asset URLs, static files always revalidated via ETag."""
    from app.version import VERSION
    with tempfile.TemporaryDirectory() as tmp:
        _, _, c = setup(tmp)
        r = c.get("/")
        assert r.headers["cache-control"] == "no-cache"
        for asset in ("app.js", "style.css", "theme.js", "js/main.js", "js/device.js"):
            assert f"\"static/{asset}?v={VERSION}\"" in r.text, asset
        assert 'id="modeBadge"' not in r.text and 'id="syncMenu"' in r.text
        r = c.get(f"/static/app.js?v={VERSION}")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and r.headers.get("etag")
        again = c.get("/static/app.js", headers={"If-None-Match": r.headers["etag"]})
        assert again.status_code == 304
        assert "cache-control" not in c.get("/api/state").headers


def test_detail_name_inline_edit_assets():
    """The name is edited in the field on the device page, the dialog and the old form are gone."""
    with tempfile.TemporaryDirectory() as tmp:
        _, _, c = setup(tmp)
        html = c.get("/").text
        assert 'id="tab-device"' in html and 'id="devActions"' in html and 'data-actions="device"' in html
        assert 'id="detail"' not in html and 'id="dFoot"' not in html, "no device dialog"
        js = all_js(c)
        assert "renameForm" not in js and "renameBlock" not in js, "altes Umbenennen-Formular entfernt"
        assert 'id="nameEdit"' not in js and 'id="nameInput"' not in js, "name only via Settings"
        assert "openDetail" not in js and "#device/" in js and "data-menu-toggle" in js
        # Editing in the Settings tab, preview before writing, without the switch a link to Settings
        for el in ("data-cfg-edit", "data-cfg-review", "data-cfg-write", "data-cfg-use", "config/preview",
                   "Config changes are off. Turn them on in Settings", "Restart now"):
            assert el in js, el


def test_device_page_data():
    """The device page gets the configuration per component and the deviations from its own type."""
    with tempfile.TemporaryDirectory() as tmp:
        fleet, _, c = setup(tmp)
        det = c.get("/api/devices/shelly2pmg4-58e6c5000202").json()
        secs = {s["id"]: s for s in det["settings"]["sections"]}
        assert list(secs)[:3] == ["sys", "wifi", "cloud"] and "cover:0" in secs
        f = {x["path"]: x for x in secs["cover:0"]["fields"]}
        assert f["cover:0.power_limit"]["kind"] == "number" and f["cover:0.power_limit"]["unit"] == "W"
        assert f["cover:0.in_mode"]["kind"] == "enum" and "detached" in f["cover:0.in_mode"]["options"]
        od = f["cover:0.obstruction_detection.enable"]
        assert od["policy"]["ok"] is False and od["policy"]["source"] == "Profile: Shutter"
        assert od["peer"] == {"majority": None, "has_majority": False}, "1:1 without majority"
        assert f["cover:0.obstruction_detection.direction"]["active"] is False, "without obstruction detection no effect"
        assert "peer" not in f["cover:0.swap_inputs"], "free via ignore in the profile"
        info = {x["path"]: x["reason"] for x in det["settings"]["info"]}
        assert info["cover:0.maxtime_open"] == "managed" and info["sys.cfg_rev"] == "read_only"
        assert info["cover:0.id"] == "read_only" and "cover:0.maxtime_open" not in f
        assert secs["cloud"]["enabled"] is True and secs["mqtt"]["enabled"] is False
        p = det["peer"]
        assert p["app"] == "S2PMG4" and p["size"] == 2
        assert {r["path"] for r in p["diffs"]} >= {"cover:0.obstruction_detection.enable"}
        assert "variants" not in p and "cover:0.swap_inputs" not in {r["path"] for r in p["diffs"]}
        assert det["device"]["updates"] == {"stable": None, "beta": None} and det["device"]["profiles"] == ["Shutter"]
        # Single device: peer group of size 1, no deviations
        assert c.get("/api/devices/shellyplus1-441793000301").json()["peer"]["size"] == 1
        from app.shelly import is_read_only
        assert all(is_read_only(m) for m in fleet.all_calls()), "the device page only reads"


def test_admin_layout_assets():
    """Sidebar navigation, dashboard with tiles, page header, relative paths for assets and API."""
    with tempfile.TemporaryDirectory() as tmp:
        _, _, c = setup(tmp)
        html = c.get("/").text
        assert 'id="sidebar"' in html and 'id="navToggle"' in html
        assert 'rr-tabs' not in html and 'data-tab=' not in html, "alte Reiterleiste entfernt"
        for page in ("dashboard", "devices", "drift", "peers", "firmware", "backups", "policy", "settings"):
            assert f'href="#{page}" data-page="{page}"' in html, page
            assert f'id="tab-{page}"' in html, page
        for el in ("cDevices", "cFindings", "cPeers", "cUpdates", "cDown", "attnTable", "typesTable", "pageTitle"):
            assert f'id="{el}"' in html, el
        assert '#devices?sort=status&amp;status=down' in html and '<option value="down">' in html
        # relative: no path starts with / (sub-path behind a proxy)
        assert '"/static/' not in html and '"/api/' not in html
        js = all_js(c)
        assert '"/api/' not in js and '`/api/' not in js
        assert "const PAGES" in js and "pushState" in js and "popstate" in js


def test_peer_comparison_only_effective_differences():
    """The overview returns hidden differences with a reason, the UI no longer has a single-device tab."""
    with tempfile.TemporaryDirectory() as tmp:
        fleet = make_fleet()
        for n, host in enumerate(("192.168.10.21", "192.168.10.22", "192.168.10.23")):
            fleet.devices[host].config["cloud"] = {"enable": False, "server": f"s{n}"}
        cfg = make_settings(tmp, shelly_password="secret-pw")
        app = create_app(cfg, transport=fleet.transport(), autostart=False)
        asyncio.run(app.state.collector.run(scan=True))
        c = TestClient(app)
        i4 = next(g for g in c.get("/api/overview").json()["peers"] if g["app"] == "PlusI4")
        assert [d["path"] for d in i4["diffs"]] == ["input:1.type"]
        assert [(h["path"], h["reason"]) for h in i4["hidden"]] == [("cloud.server", "no_effect")]
        # Own policy without peer_depends: the built-in default applies; peer_depends: [] turns it off
        assert c.put("/api/policy", json={"text": "baseline: {}\n"}, headers=H).status_code == 200
        i4 = next(g for g in c.get("/api/overview").json()["peers"] if g["app"] == "PlusI4")
        assert i4["hidden"][0]["path"] == "cloud.server"
        assert c.put("/api/policy", json={"text": "peer_depends: []\n"}, headers=H).status_code == 200
        i4 = next(g for g in c.get("/api/overview").json()["peers"] if g["app"] == "PlusI4")
        assert "cloud.server" in [d["path"] for d in i4["diffs"]]
        r = c.put("/api/policy", json={"text": "peer_depends: [{paths: [a], when: {b: {max: 1}}}]\n"}, headers=H)
        assert r.status_code == 422 and "only a value" in r.json()["errors"][0]
        js = all_js(c)
        assert "Single devices" not in js and "All device types are uniform" in js and "difference${hs.length" in js
        assert "rr-fw-table" in js, "firmware tables with fixed column widths"


def test_sidebar_layout_setting():
    """Default Tabler "Vertical" (always expanded), with "Compact sidebar" the icon bar (folded hover)."""
    with tempfile.TemporaryDirectory() as tmp:
        _, _, c = setup(tmp)
        vertical = 'class="navbar navbar-vertical navbar-expand-lg d-print-none" id="sidebar"'
        assert vertical in c.get("/").text
        assert c.get("/api/state").json()["sidebar_folded"] is False
        f = {x["key"]: x for x in c.get("/api/settings").json()["fields"]}
        assert f["sidebar_folded"]["group"] == "Appearance" and f["sidebar_folded"]["env"] == "SIDEBAR_FOLDED"
        assert c.put("/api/settings", json={"settings": {"sidebar_folded": True}}, headers=H).status_code == 200
        html = c.get("/").text
        assert 'class="navbar navbar-vertical navbar-expand-lg navbar-folded-hover d-print-none" id="sidebar"' in html
        assert c.get("/api/state").json()["sidebar_folded"] is True
        assert c.put("/api/settings", json={"settings": {"sidebar_folded": None}}, headers=H).status_code == 200
        assert vertical in c.get("/").text



def test_stack_badges():
    """Matter/Zigbee only with the stack enabled, with connection status."""
    with tempfile.TemporaryDirectory() as tmp:
        fleet = make_fleet(stacks=True)
        app = create_app(make_settings(tmp, shelly_password="secret-pw"), transport=fleet.transport(), autostart=False)
        asyncio.run(app.state.collector.run(scan=True))
        devs = {d["id"]: d for d in TestClient(app).get("/api/overview").json()["devices"]}
        assert devs["shelly1pmg4-1020bafd4410"]["stacks"] == [
            {"stack": "zigbee", "label": "Zigbee", "connected": True, "detail": "Zigbee network: joined"}]
        assert devs["shelly1pmg4-1020bafd4420"]["stacks"] == [
            {"stack": "matter", "label": "Matter", "connected": None, "detail": "Matter enabled (num_fabrics: 1)"}]
        assert devs["shelly2pmg4-1020ba000201"]["stacks"] == [], "no badge without stack component"


def test_audit_log():
    """Audit across all devices incl. Settings and policy, filters, CSV; Settings without values."""
    from app import policy as pol
    with tempfile.TemporaryDirectory() as tmp:
        fleet, app, c = setup(tmp)
        assert c.put("/api/settings", json={"settings": {"allow_config": True}}, headers=H).status_code == 200
        text = open(pol.DEFAULT_POLICY_PATH, encoding="utf-8").read()
        assert c.put("/api/policy", json={"text": text}, headers=H).status_code == 200
        app.state.collector.store.add_audit("shellyplus1-441793000301", "rename", "a", "b", True)
        body = c.get("/api/audit").json()
        assert {"settings", "policy", "rename"} <= set(body["actions"])
        first = body["entries"][0]
        assert first["action"] == "rename" and first["device_name"], "newest first, with device name"
        st = next(e for e in body["entries"] if e["action"] == "settings")
        assert st["device_id"] == "" and st["detail"] == "allow_config" and st["new"] is None
        only = c.get("/api/audit", params={"action": "policy"}).json()["entries"]
        assert only and all(e["action"] == "policy" for e in only)
        fleet_only = c.get("/api/audit", params={"device_id": ""}).json()["entries"]
        assert {e["action"] for e in fleet_only} == {"settings", "policy"}
        r = c.get("/api/audit.csv")
        assert r.status_code == 200 and r.text.startswith("at,device_id,device_name,action") and "rename" in r.text


def test_list_methods():
    """Shelly.ListMethods per firmware version; missing methods block restart and SetConfig."""
    plus1 = "shellyplus1-441793000301"
    fleet = make_fleet()
    dev = fleet.devices["192.168.10.41"]
    dev.methods = ["Shelly.GetDeviceInfo", "Shelly.GetConfig", "Shelly.ListMethods", "Shelly.Update", "Sys.SetConfig",
                   "Wifi.SetConfig"]   # spelling as on real devices
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(make_settings(tmp, shelly_password="secret-pw", allow_reboot=True, allow_config=True),
                         transport=fleet.transport(), autostart=False)
        asyncio.run(app.state.collector.run(scan=True))
        c = TestClient(app)
        rows = {d["id"]: d for d in c.get("/api/overview").json()["devices"]}
        assert rows[plus1]["caps"] == {"update": True, "reboot": False, "set_auth": False}
        assert rows["shellyplusi4-b8d61a000101"]["caps"] is None, "without ListMethods: unknown"
        det = c.get(f"/api/devices/{plus1}").json()
        assert det["methods"] == sorted(dev.methods) and det["device"]["caps"]["reboot"] is False
        r = c.post(f"/api/devices/{plus1}/reboot", headers=H)
        assert r.status_code == 502 and "does not offer Shelly.Reboot" in r.text and dev.reboots == []
        body = c.post("/api/apply/preview", json={"mode": "bulk", "path": "switch:0.initial_state", "value": "off",
                                                  "device_ids": [plus1]}, headers=H).json()
        assert "does not offer Switch.SetConfig" in json.dumps(body), body
        assert app.state.collector.supports(plus1, "WiFi.SetConfig"), "case does not matter"
        calls = dev.calls.count("Shelly.ListMethods")
        asyncio.run(app.state.collector.run(scan=False))
        assert dev.calls.count("Shelly.ListMethods") == calls, "only once per firmware version"


def test_refresh_one_device():
    """Sync on the device page reads only this device."""
    with tempfile.TemporaryDirectory() as tmp:
        fleet, app, c = setup(tmp)
        dev = fleet.devices["192.168.10.41"]
        other = fleet.devices["192.168.10.21"]
        dev.config["sys"]["sntp"]["server"] = "ntp.example"
        before_other = len(other.calls)
        r = c.post("/api/devices/shellyplus1-441793000301/refresh", headers=H)
        assert r.status_code == 200 and r.json()["ok"] is True and r.json()["changed"] is True, r.text
        assert len(other.calls) == before_other, "other devices stay untouched"
        assert c.post("/api/devices/shellyplus1-441793000301/refresh").status_code == 403, "CSRF"
        assert c.post("/api/devices/nope/refresh", headers=H).status_code == 404


def test_api_docs():
    """OpenAPI and Swagger UI under /api/docs, no longer public under /openapi.json."""
    with tempfile.TemporaryDirectory() as tmp:
        _, _, c = setup(tmp)
        assert c.get("/openapi.json").status_code == 404
        spec = c.get("/api/openapi.json").json()
        assert spec["info"]["title"] == "RelayRoster" and "/api/overview" in spec["paths"]
        r = c.get("/api/docs")
        assert r.status_code == 200 and "swagger" in r.text.lower() and "openapi.json" in r.text


def test_policy_conflict():
    """Saving with an outdated version is rejected with 409, without base_hash as before."""
    with tempfile.TemporaryDirectory() as tmp:
        _, _, c = setup(tmp)
        first = c.get("/api/policy").json()
        text = first["text"]
        r = c.put("/api/policy", json={"text": text + "\n# A\n", "base_hash": first["hash"]}, headers=H)
        assert r.status_code == 200 and r.json()["hash"] == c.get("/api/policy").json()["hash"]
        r = c.put("/api/policy", json={"text": text + "\n# B\n", "base_hash": first["hash"]}, headers=H)
        assert r.status_code == 409 and r.json()["conflict"] is True
        assert "# A" in c.get("/api/policy").json()["text"], "the newer version stays"
        assert c.put("/api/policy", json={"text": text}, headers=H).status_code == 200, "without base_hash as before"

def test_safe_defaults():
    """Write switches off and no fixed network by default; open app is flagged; security headers everywhere."""
    from app.config import Settings
    env = {k: v for k, v in os.environ.items()}
    for k in ("SCAN_NETWORKS", "ALLOW_CONFIG", "ALLOW_FIRMWARE_UPDATE", "ALLOW_REBOOT", "ALLOW_SET_PASSWORD"):
        os.environ.pop(k, None)
    try:
        d = Settings()
    finally:
        os.environ.clear(); os.environ.update(env)
    assert d.scan_networks == [], "no network is guessed"
    assert not (d.allow_config or d.allow_firmware_update or d.allow_reboot or d.allow_set_password)
    with tempfile.TemporaryDirectory() as tmp:
        fleet, app, c = setup(tmp, scan_networks=[], extra_hosts=[])
        st = c.get("/api/state").json()
        assert st["auth"] is False, "the UI shows the no-sign-in warning"
        assert any("No networks configured" in w for w in st["scan_warnings"])
        for path in ("/", "/api/state", "/static/app.js"):
            r = c.get(path)
            assert r.headers["x-content-type-options"] == "nosniff", path
            assert r.headers["x-frame-options"] == "DENY", path
            assert "frame-ancestors 'none'" in r.headers["content-security-policy"], path
        html = c.get("/").text
        assert 'id="authWarn"' in html


if __name__ == "__main__":
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
