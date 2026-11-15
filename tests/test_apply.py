"""Apply profile, bulk change, assignment and missing settings.

Usage: python tests/test_apply.py   (or pytest)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time

os.environ["RELAYROSTER_NO_AUTOAPP"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient  # noqa: E402

from app import policy as pol  # noqa: E402
from app.main import create_app  # noqa: E402
from fake_shelly import make_fleet  # noqa: E402
from test_core import make_settings  # noqa: E402

H = {"X-Requested-With": "relayroster"}
I4A, I4B, I4C = "shellyplusi4-b8d61a000101", "shellyplusi4-b8d61a000102", "shellyplusi4-b8d61a000103"
G4A, G4B = "shelly2pmg4-1020ba000201", "shelly2pmg4-58e6c5000202"
PLUS1, MINI = "shellyplus1-441793000301", "shelly1minig3-84fce6000401"


def make(tmp: str, fleet=None, policy: str | None = None, **kw):
    fleet = fleet or make_fleet()
    cfg = make_settings(tmp, shelly_password="secret-pw", **kw)
    if policy is not None:
        pol.write_text(cfg.policy_path, policy)
    app = create_app(cfg, transport=fleet.transport(), autostart=False)
    asyncio.run(app.state.collector.run(scan=True))
    return fleet, app


def wait_job(c: TestClient) -> dict:
    for _ in range(200):
        job = c.get("/api/apply/job").json()["job"]
        if job and not job["running"]:
            return job
        time.sleep(0.05)
    raise AssertionError("job does not finish")


def by_host(fleet, dev_id):
    return next(d for d in fleet.devices.values() if d.id == dev_id)


def test_missing_settings_are_classified():
    """Not every Shelly has every setting: outdated rule, does not apply to the model, older firmware."""
    fleet = make_fleet()
    for d in fleet.devices.values():
        if d.id.startswith("shellyplusi4"):
            d.config["sys"]["device"].pop("eco_mode")      # exists on no i4: n/a
    by_host(fleet, G4B).config["sys"]["device"].pop("eco_mode")   # g4a has it: a real gap
    by_host(fleet, G4A).ver = "2.2.0"
    text = ("baseline:\n  sys.device.eco_mode: false\n  sys.legacy.flag: false   # exists nowhere\n")
    with tempfile.TemporaryDirectory() as tmp:
        _, app = make(tmp, fleet, text)
        c = TestClient(app)
        ov = c.get("/api/overview").json()
        assert [(s["source"], s["path"]) for s in ov["stale_rules"]] == [("Baseline", "sys.legacy.flag")]
        assert len(ov["stale_rules"][0]["devices"]) == 7
        paths = {(f["device_id"], f["path"]) for f in ov["findings"]}
        assert not any(p == "sys.legacy.flag" for _, p in paths)
        assert not any(d.startswith("shellyplusi4") and p == "sys.device.eco_mode" for d, p in paths)
        miss = next(f for f in ov["findings"] if f["device_id"] == G4B and f["path"] == "sys.device.eco_mode")
        assert miss["kind"] == "missing" and "firmware 2.2.0" in miss["note"] and "Update" in miss["note"]
        row = next(r for r in ov["devices"] if r["id"] == I4A)
        assert row["findings"] == sum(1 for f in ov["findings"] if f["device_id"] == I4A)
        det = c.get(f"/api/devices/{I4A}").json()
        assert [n["path"] for n in det["not_applicable"]] == ["sys.device.eco_mode"]

        r = c.post("/api/policy/rule/remove", json={"source": "Baseline", "path": "sys.legacy.flag"}, headers=H)
        assert r.status_code == 200, r.text
        txt = pol.read_text(app.state.settings.policy_path)
        assert "sys.legacy.flag" not in txt and "sys.device.eco_mode: false" in txt
        assert c.get("/api/overview").json()["stale_rules"] == []


def test_bulk_change_for_whole_fleet():
    """NTP server for all devices: preview with exact calls, device without the setting is skipped,
    baseline rule with comment, job writes one after the other, audit per device."""
    fleet = make_fleet()
    by_host(fleet, MINI).config["sys"].pop("sntp")
    by_host(fleet, PLUS1).config["sys"]["sntp"]["server"] = "router.example.com"
    with tempfile.TemporaryDirectory() as tmp:
        _, app = make(tmp, fleet, allow_config=False)
        with TestClient(app) as c:
            paths = c.get("/api/bulk/paths").json()["paths"]
            sntp = next(p for p in paths if p["path"] == "sys.sntp.server")
            assert sntp["values"][0] == {"value": "time.google.com", "count": 5} and len(sntp["devices"]) == 6
            assert not any(p["path"].startswith("wifi.sta") or p["path"] == "sys.device.name" for p in paths)
            assert any(p["path"] == "cover:*.power_limit" for p in paths)

            ids = [d["id"] for d in app.state.store.devices()]
            body = {"mode": "bulk", "path": "sys.sntp.server", "value": "router.example.com", "device_ids": ids}
            pv = c.post("/api/apply/preview", json=body, headers=H).json()
            assert pv["ok"]
            st = {d["device_id"]: d["rows"][0]["status"] for d in pv["devices"]}
            assert st[MINI] == "unsupported" and st[PLUS1] == "ok" and st[I4A] == "change"
            g4 = next(d for d in pv["devices"] if d["device_id"] == G4A)
            assert g4["calls"] == [{"component": "sys", "method": "Sys.SetConfig",
                                    "params": {"config": {"sntp": {"server": "router.example.com"}}},
                                    "paths": ["sys.sntp.server"]}]
            items = [{"device_id": d["device_id"], "changes": d["changes"]} for d in pv["devices"] if d["changes"]]
            assert len(items) == 5
            start = {"items": items, "title": "NTP", "policy": {"scope": "baseline", "path": "sys.sntp.server",
                                                               "value": "router.example.com", "comment": "Bulk change"}}
            assert c.post("/api/apply", json=start, headers=H).status_code == 403   # switch off
            app.state.settings.allow_config = True
            r = c.post("/api/apply", json=start, headers=H)
            assert r.status_code == 202, r.text
            job = wait_job(c)
            assert job["counts"] == {"done": 5}, job
            for dev_id in (I4A, I4B, I4C, G4A, G4B):
                assert by_host(fleet, dev_id).config["sys"]["sntp"]["server"] == "router.example.com"
                assert any(a["action"] == "config" and a["ok"] for a in app.state.store.audit(dev_id))
            assert "sntp" not in by_host(fleet, MINI).config["sys"]
            assert not any(m == "Sys.SetConfig" for m, _ in by_host(fleet, PLUS1).config_writes)
            txt = pol.read_text(app.state.settings.policy_path)
            assert "sys.sntp.server: router.example.com # Bulk change" in txt
            ov = c.get("/api/overview").json()
            assert not any(f["path"] == "sys.sntp.server" for f in ov["findings"])


def test_profile_apply_moving_cover_and_restart():
    """Apply profile: only fixed targets, operator rules as a hint, a moving shutter is skipped."""
    fleet = make_fleet()
    by_host(fleet, G4A).config["cover:0"]["initial_state"] = "open"
    by_host(fleet, G4A).cover_status["cover:0"] = {"state": "opening"}
    with tempfile.TemporaryDirectory() as tmp:
        _, app = make(tmp, fleet, allow_config=True)   # default policy with profile Shutter
        with TestClient(app) as c:
            pv = c.post("/api/apply/preview", json={"mode": "profile", "profile": "Shutter"}, headers=H).json()
            assert pv["ok"], pv
            assert sorted(d["device_id"] for d in pv["devices"]) == [G4A, G4B]
            b = next(d for d in pv["devices"] if d["device_id"] == G4B)
            assert b["changes"] == {"cover:0.obstruction_detection.enable": True}
            rows = {r["path"]: r for r in b["rows"]}
            assert rows["cover:0.power_limit"]["status"] == "ok"
            assert rows["cover:0.in_mode"]["status"] == "ok"
            a = next(d for d in pv["devices"] if d["device_id"] == G4A)
            assert a["changes"] == {"cover:0.initial_state": "stopped"}
            items = [{"device_id": d["device_id"], "changes": d["changes"]} for d in pv["devices"]]
            r = c.post("/api/apply", json={"items": items, "title": "Apply Shutter"}, headers=H)
            assert r.status_code == 202, r.text
            job = wait_job(c)
            st = {i["device_id"]: i for i in job["items"]}
            assert st[G4A]["status"] == "skipped" and "moving" in st[G4A]["error"]
            assert st[G4B]["status"] == "done"
            assert by_host(fleet, G4B).config["cover:0"]["obstruction_detection"]["enable"] is True
            assert by_host(fleet, G4A).config["cover:0"]["initial_state"] == "open"
            # Operator rule without a fixed value: hint, no call
            by_host(fleet, G4B).config["cover:0"]["power_limit"] = 2800
            asyncio.run(app.state.collector.run())
            pv = c.post("/api/apply/preview", json={"mode": "profile", "profile": "Shutter",
                                                    "device_ids": [G4B]}, headers=H).json()
            row = next(r for r in pv["devices"][0]["rows"] if r["path"] == "cover:0.power_limit")
            assert row["status"] == "hint" and pv["devices"][0]["changes"] == {}


def test_assign_devices_to_profile():
    """Assign without YAML: devices and exclude, the device page shows how a profile applies."""
    with tempfile.TemporaryDirectory() as tmp:
        _, app = make(tmp)
        c = TestClient(app)
        url = "/api/profiles/Button input/devices"
        assert c.post(url, json={"add": [PLUS1], "remove": [I4A]}, headers=H).status_code == 200
        data = pol.load(app.state.settings.policy_path)
        prof = next(p for p in data["profiles"] if p["name"] == "Button input")
        assert prof["devices"] == [PLUS1] and prof["exclude"] == [I4A]
        rows = {r["id"]: r for r in c.get("/api/overview").json()["devices"]}
        assert "Button input" in rows[PLUS1]["profiles"] and "Button input" not in rows[I4A]["profiles"]
        via = {a["name"]: a["via"] for a in c.get(f"/api/devices/{PLUS1}").json()["profile_assignment"]}
        assert via == {"Shutter": None, "Button input": "assigned"}
        via = {a["name"]: a["via"] for a in c.get(f"/api/devices/{I4A}").json()["profile_assignment"]}
        assert via["Button input"] == "excluded"
        # back: file as before
        assert c.post(url, json={"add": [I4A], "remove": [PLUS1]}, headers=H).status_code == 200
        prof = next(p for p in pol.load(app.state.settings.policy_path)["profiles"] if p["name"] == "Button input")
        assert "devices" not in prof and "exclude" not in prof
        assert c.post(url, json={"add": ["nope"]}, headers=H).status_code == 404
        # Profile only with assignment, without match
        r = c.post("/api/profiles", json={"profile": {"name": "Fountain", "devices": [MINI],
                                                      "settings": [{"path": "sys.device.eco_mode", "op": "=",
                                                                    "value": True, "comment": ""}]}}, headers=H)
        assert r.status_code == 200, r.text
        assert "Fountain" in {r["id"]: r for r in c.get("/api/overview").json()["devices"]}[MINI]["profiles"]
        r = c.post("/api/profiles/Fountain/devices", json={"remove": [MINI]}, headers=H)
        assert r.status_code == 422 and "at least one device" in r.json()["errors"][0]



def test_restart_many():
    """Restart devices one after the other; a moving shutter is skipped; only with the switch."""
    fleet = make_fleet()
    by_host(fleet, G4A).cover_status["cover:0"] = {"state": "opening"}
    with tempfile.TemporaryDirectory() as tmp:
        _, app = make(tmp, fleet)
        with TestClient(app) as c:
            r = c.post("/api/restart", json={"device_ids": [I4A]}, headers=H)
            assert r.status_code == 403, "blocked without Allow restarting devices"
    with tempfile.TemporaryDirectory() as tmp:
        _, app = make(tmp, fleet, allow_reboot=True)
        app.state.collector.reboot_poll_s, app.state.collector.reboot_timeout_s = 0.01, 0.5   # otherwise ~6 s per device
        with TestClient(app) as c:
            r = c.post("/api/restart", json={"device_ids": [I4A, G4A, I4B]}, headers=H)
            assert r.status_code == 202, r.text
            job = wait_job(c)
            assert job["restart_only"] is True
            st = {it["device_id"]: it for it in job["items"]}
            assert st[I4A]["status"] == "done" and st[I4A]["restarted"] and st[I4B]["status"] == "done"
            assert st[G4A]["status"] == "skipped" and "moving" in st[G4A]["error"]
            assert len(by_host(fleet, I4A).reboots) == 1 and by_host(fleet, G4A).reboots == []
            assert not any(m.endswith("SetConfig") for m in fleet.all_calls()), "writes nothing"
            assert c.post("/api/restart", json={"device_ids": []}, headers=H).status_code == 422
            assert c.post("/api/restart", json={"device_ids": ["nope"]}, headers=H).status_code == 404


def test_restore_config():
    """Play back the configuration from an older backup: preview with changes, job writes, audit."""
    fleet = make_fleet()
    dev = by_host(fleet, PLUS1)
    before = dev.config["sys"]["sntp"]["server"]
    with tempfile.TemporaryDirectory() as tmp:
        _, app = make(tmp, fleet, allow_config=True)
        dev.config["sys"]["sntp"]["server"] = "ntp.example"          # changed on the device, new snapshot
        asyncio.run(app.state.collector.run(scan=False))
        with TestClient(app) as c:
            snaps = c.get(f"/api/devices/{PLUS1}").json()["snapshots"]
            assert len(snaps) >= 2
            r = c.post("/api/apply/preview", json={"mode": "restore", "snapshot_id": snaps[-1]["id"]}, headers=H)
            assert r.status_code == 200, r.text
            plan = r.json()["devices"][0]
            rows = {x["path"]: x for x in plan["rows"]}
            assert rows["sys.sntp.server"]["status"] == "change" and rows["sys.sntp.server"]["new"] == before
            assert "sys.cfg_rev" not in rows, "read-only values are missing from the preview"
            assert plan["changes"] == {"sys.sntp.server": before}
            r = c.post("/api/apply", json={"items": [{"device_id": PLUS1, "changes": plan["changes"]}],
                                           "title": "Restore"}, headers=H)
            assert r.status_code == 202, r.text
            assert wait_job(c)["counts"] == {"done": 1}
            assert dev.config["sys"]["sntp"]["server"] == before
            latest = c.get(f"/api/devices/{PLUS1}").json()["snapshots"][0]["id"]
            r = c.post("/api/apply/preview", json={"mode": "restore", "snapshot_id": latest}, headers=H)
            assert r.json()["devices"][0]["rows"] == [], "current state: nothing to play back"
            assert c.post("/api/apply/preview", json={"mode": "restore", "snapshot_id": 99999},
                          headers=H).status_code == 404


def test_set_password():
    """Password from Settings on devices without password; protected ones are skipped; only with the switch."""
    import hashlib
    from app.shelly import is_auth_call
    good = {"user": "admin", "realm": "x", "ha1": "a" * 64}
    assert is_auth_call("Shelly.SetAuth", good)
    assert not is_auth_call("Shelly.SetAuth", {**good, "ha1": None}), "disabling the password stays blocked"
    assert not is_auth_call("Shelly.SetAuth", {**good, "user": "root"})
    assert not is_auth_call("Shelly.SetAuth", {**good, "extra": 1})
    fleet = make_fleet()
    with tempfile.TemporaryDirectory() as tmp:
        _, app = make(tmp, fleet)
        with TestClient(app) as c:
            assert c.post("/api/auth/rollout", json={"device_ids": [PLUS1]}, headers=H).status_code == 403
    with tempfile.TemporaryDirectory() as tmp:
        _, app = make(tmp, fleet, allow_set_password=True)
        with TestClient(app) as c:
            r = c.post("/api/auth/rollout", json={"device_ids": [PLUS1, MINI]}, headers=H)
            assert r.status_code == 202, r.text
            job = wait_job(c)
            st = {it["device_id"]: it for it in job["items"]}
            assert job["auth_only"] is True and st[PLUS1]["status"] == "done", st
            assert st[MINI]["status"] == "skipped" and st[MINI]["error"] == "Password already set"
            dev = by_host(fleet, PLUS1)
            assert dev.auth_ha1 == hashlib.sha256(f"admin:{PLUS1}:secret-pw".encode()).hexdigest()
            det = c.get(f"/api/devices/{PLUS1}").json()
            assert det["device"]["auth_en"] is True
            assert det["audit"][0]["action"] == "auth" and "secret-pw" not in json.dumps(det["audit"])

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
