"""Core logic against the simulated fleet: read-only, scan, snapshots, policy, peer comparison.

Usage: python tests/test_core.py   (or pytest)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import httpx  # noqa: E402

from app import compare, discovery, models, policy as pol  # noqa: E402
from app.collector import Collector  # noqa: E402
from app.config import Settings  # noqa: E402
from app.collector import RenameError  # noqa: E402
from app.shelly import (  # noqa: E402
    ShellyClient, WriteBlocked, clean_name, is_firmware_call, is_read_only, is_reboot_call, is_rename_call,
)
from app.store import Store  # noqa: E402
from fake_shelly import make_fleet  # noqa: E402


def make_settings(tmp: str, **kw) -> Settings:
    s = Settings()
    s.data_dir = tmp
    s.scan_networks = ["192.168.10.0/24"]
    s.extra_hosts = []
    s.scan_timeout_s = 0.5
    s.snapshots_keep = 5
    s.shelly_password = ""
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def run_collector(fleet, tmp, scan=True, **kw):
    s = make_settings(tmp, **kw)
    store = Store(s.db_path)
    col = Collector(store, s, transport=fleet.transport())
    summary = asyncio.run(col.run(scan=scan))
    return store, col, summary


# ---- Read-only ------------------------------------------------------------------------------

def test_read_only_guard():
    for m in ("Shelly.GetConfig", "Shelly.GetStatus", "Script.List", "Script.GetCode", "KVS.GetMany", "Schedule.List"):
        assert is_read_only(m), m
    for m in ("Sys.SetConfig", "Shelly.Update", "Shelly.Reboot", "Shelly.FactoryReset", "Script.PutCode",
              "KVS.Set", "Cover.Open", "Switch.Set", "Shelly.SetAuth", "Shelly.CheckForUpdate", "Script.Delete",
              "Webhook.Create", "Schedule.DeleteAll", "Getter", "Sys.GetConfig; Sys.SetConfig"):
        assert not is_read_only(m), m

    hit = []

    def handler(request):
        hit.append(request)
        return httpx.Response(200, json={"id": 1, "result": {}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            c = ShellyClient(http, "192.168.10.5")
            try:
                await c.call("Sys.SetConfig", {"config": {}})
            except WriteBlocked:
                return True
            return False

    assert asyncio.run(go()) is True
    assert hit == [], "a blocked call must never reach the network"


def test_collector_only_reads():
    fleet = make_fleet()
    with tempfile.TemporaryDirectory() as tmp:
        run_collector(fleet, tmp)
    calls = fleet.all_calls()
    assert calls, "the fake was not queried"
    bad = [c for c in calls if not is_read_only(c)]
    assert not bad, f"writing calls: {bad}"


# ---- Renaming: the only write action -----------------------------------------------

def _rename_params(name):
    return {"config": {"device": {"name": name}}}


def test_firmware_guard():
    assert is_firmware_call("Shelly.CheckForUpdate", None) and is_firmware_call("Shelly.CheckForUpdate", {})
    assert is_firmware_call("Shelly.Update", {"stage": "stable"}) and is_firmware_call("Shelly.Update", {"stage": "beta"})
    for method, params in [
        ("Shelly.Update", {"url": "http://evil/fw.zip"}),             # fremde Firmware-Quelle
        ("Shelly.Update", {"stage": "stable", "url": "http://x"}),   # additional key
        ("Shelly.Update", {"stage": "nightly"}),
        ("Shelly.Update", None),
        ("Shelly.CheckForUpdate", {"x": 1}),
        ("Shelly.Reboot", {}),
        ("OTA.Revert", {}),
    ]:
        assert not is_firmware_call(method, params), (method, params)

    async def go():
        async with httpx.AsyncClient(transport=make_fleet().transport()) as http:
            cl = ShellyClient(http, "192.168.10.21")
            try:
                await cl.update_firmware("nightly")
            except WriteBlocked:
                pass
            else:
                raise AssertionError("nightly must not pass")
            try:
                await cl._send("Shelly.Update", {"url": "http://evil/fw.zip"})
            except WriteBlocked:
                return True
    assert asyncio.run(go())


def test_reboot_guard():
    """Shelly.Reboot only without parameters and only via reboot(), never via call()."""
    assert is_reboot_call("Shelly.Reboot", None) and is_reboot_call("Shelly.Reboot", {})
    for method, params in [("Shelly.Reboot", {"delay_ms": 5000}), ("Shelly.FactoryReset", None),
                           ("Sys.Reboot", None), ("Shelly.ResetWiFiConfig", None)]:
        assert not is_reboot_call(method, params), (method, params)
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": 1, "result": None})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            c = ShellyClient(http, "192.168.10.5")
            for attempt in (lambda: c.call("Shelly.Reboot"), lambda: c._send("Shelly.Reboot", {"delay_ms": 1})):
                try:
                    await attempt()
                    raise AssertionError("must be blocked")
                except WriteBlocked:
                    pass
            assert sent == [], "blocked calls do not reach the network"
            await c.reboot()
    asyncio.run(go())
    assert len(sent) == 1 and sent[0]["method"] == "Shelly.Reboot" and "params" not in sent[0]


def test_config_guard():
    """*.SetConfig only with fields from the allow list, id exactly for indexed components, never via call()."""
    from app import devicepage, writable
    from app.shelly import is_config_call
    ok = [
        ("Cover.SetConfig", {"id": 0, "config": {"power_limit": 280}}),
        ("Cover.SetConfig", {"id": 1, "config": {"obstruction_detection": {"enable": True, "action": "stop"}}}),
        ("Sys.SetConfig", {"config": {"location": {"tz": "Europe/Berlin"}, "device": {"eco_mode": True}}}),
        ("WiFi.SetConfig", {"config": {"ap": {"enable": False}}}),
        ("MQTT.SetConfig", {"config": {"enable": True, "server": "10.0.0.1:1883", "ssl_ca": None}}),
        ("Input.SetConfig", {"id": 2, "config": {"type": "button", "name": None}}),
    ]
    for method, params in ok:
        assert is_config_call(method, params), (method, params)
    bad = [
        ("WiFi.SetConfig", {"config": {"sta": {"ssid": "x", "pass": "y"}}}),          # WLAN-Client
        ("WiFi.SetConfig", {"config": {"ap": {"pass": "y"}}}),                         # Passwort
        ("MQTT.SetConfig", {"config": {"pass": "y"}}),
        ("Sys.SetConfig", {"config": {"device": {"profile": "cover"}}}),
        ("Sys.SetConfig", {"config": {"cfg_rev": 9}}),
        ("Cover.SetConfig", {"id": 0, "config": {"maxtime_open": 30}}),                # Kalibrierung
        ("Cover.SetConfig", {"id": 0, "config": {"obstruction_detection": {"power_thr": 100}}}),
        ("Cover.SetConfig", {"id": 0, "config": {"motor": {"idle_power_thr": 2}}}),
        ("Cover.SetConfig", {"config": {"power_limit": 280}}),                         # id missing
        ("Cover.SetConfig", {"id": "0", "config": {"power_limit": 280}}),
        ("Cover.SetConfig", {"id": True, "config": {"power_limit": 280}}),
        ("Cover.SetConfig", {"id": 0, "config": {"power_limit": 280}, "x": 1}),
        ("Cover.SetConfig", {"id": 0, "config": {}}),
        ("Cover.SetConfig", {"id": 0, "config": {"power_limit": [280]}}),
        ("Cloud.SetConfig", {"id": 0, "config": {"enable": True}}),                    # id on a non-indexed one
        ("Cloud.SetConfig", {"config": {"server": "evil"}}),
        ("BLE.SetConfig", {"config": {"name": "x"}}),                                 # only on/off
        ("Script.SetConfig", {"id": 1, "config": {"enable": True}}),
        ("Cover.Open", {"id": 0}),
        ("Cover.SetConfig", None),
    ]
    for method, params in bad:
        assert not is_config_call(method, params), (method, params)
    assert is_config_call("Sys.SetConfig", {"config": {"device": {"name": "x"}}}), "name via SetConfig"
    # Allow list: only fields from the schema, never READ_ONLY_PATHS or MANAGED_PATHS
    for kind, keys in writable.EDITABLE.items():
        assert kind in writable.METHODS
        for key in keys:
            assert key in devicepage.SCHEMA[kind]["fields"], (kind, key)
            path = f"{kind}:0.{key}" if kind in writable.INDEXED else f"{kind}.{key}"
            assert writable.is_editable(path), path
            assert not compare.matches_any(compare.READ_ONLY_PATHS + compare.MANAGED_PATHS, path), path
            assert not key.endswith("pass") and not key.startswith(("sta.", "sta1.")), path
    assert not writable.is_editable("cover.power_limit") and not writable.is_editable("cloud:0.enable")
    assert not writable.is_editable("cover:x.power_limit")
    # Check values
    num = {"kind": "number"}
    assert writable.validate("p", 280.0, num, 300) == 280 and isinstance(writable.validate("p", 280.0, num, 300), int)
    for value, meta in [(-1, num), (True, num), ("280", num), (float("nan"), num), ("x", {"kind": "bool"}),
                        ("nightly", {"kind": "enum", "options": ["a"]}), ("a\nb", {"kind": "text"}),
                        ("x" * 256, {"kind": "text"}), (1, {"kind": "text"}), (1, {"kind": "object"}),
                        (100, {"kind": "number", "min": -90, "max": 90})]:
        try:
            writable.validate("p", value, meta, None)
        except ValueError:
            continue
        raise AssertionError((value, meta))
    calls = writable.build_calls({"cover:0.power_limit": 280, "cover:0.obstruction_detection.enable": True,
                                  "sys.location.tz": "UTC"})
    assert [c["method"] for c in calls] == ["Cover.SetConfig", "Sys.SetConfig"]
    assert calls[0]["params"] == {"id": 0, "config": {"obstruction_detection": {"enable": True}, "power_limit": 280}}
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": 1, "result": {"restart_required": False}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            c = ShellyClient(http, "192.168.10.5")
            for attempt in (lambda: c.call("Cover.SetConfig", {"id": 0, "config": {"power_limit": 1}}),
                            lambda: c.set_config("WiFi.SetConfig", {"config": {"sta": {"enable": False}}}),
                            lambda: c._send("Cover.SetConfig", {"id": 0, "config": {"maxtime_open": 1}})):
                try:
                    await attempt()
                    raise AssertionError("must be blocked")
                except WriteBlocked:
                    pass
            assert sent == [], "blocked calls do not reach the network"
            await c.set_config("Cover.SetConfig", {"id": 0, "config": {"power_limit": 280}})
    asyncio.run(go())
    assert sent[0]["method"] == "Cover.SetConfig" and sent[0]["params"] == {"id": 0, "config": {"power_limit": 280}}


def test_rename_guard():
    assert is_rename_call("Sys.SetConfig", _rename_params("Kitchen Wall Switch"))
    for method, params in [
        ("Sys.SetConfig", {"config": {"device": {"name": "x", "eco_mode": True}}}),   # additional key
        ("Sys.SetConfig", {"config": {"device": {"name": "x"}, "location": {"tz": "UTC"}}}),
        ("Sys.SetConfig", {"config": {"device": {"name": "x"}}, "extra": 1}),
        ("Sys.SetConfig", {"config": {"debug": {"name": "x"}}}),
        ("Switch.SetConfig", _rename_params("x")),
        ("Sys.SetConfig", _rename_params("")),
        ("Sys.SetConfig", _rename_params(" padded ")),                                # not trimmed
        ("Sys.SetConfig", _rename_params("a\nb")),
        ("Sys.SetConfig", _rename_params("x" * 65)),
        ("Sys.SetConfig", _rename_params(42)),
        ("Sys.SetConfig", None),
    ]:
        assert not is_rename_call(method, params), (method, params)
    assert clean_name("  Garden Fountain ") == "Garden Fountain"
    assert clean_name("Children's Room Shutter") == "Children's Room Shutter"
    for bad in ("", "   ", "x" * 65, "tab\there", 7, None):
        try:
            clean_name(bad)
            raise AssertionError(f"{bad!r} must be rejected")
        except ValueError:
            pass

    hit = []

    def handler(request):
        hit.append(request)
        return httpx.Response(200, json={"id": 1, "result": {"restart_required": False}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            c = ShellyClient(http, "192.168.10.5")
            # call() stays read-only, also for the allowed name form
            try:
                await c.call("Sys.SetConfig", _rename_params("x"))
                raise AssertionError("call() must not write")
            except WriteBlocked:
                pass
            try:
                await c._send("Sys.SetConfig", {"config": {"wifi": {"ap": {"enable": True}}}})
                raise AssertionError("_send() must refuse other writes")
            except WriteBlocked:
                pass
            for bad in ("", "a\x00b", "x" * 65):
                try:
                    await c.set_name(bad)
                    raise AssertionError("invalid name must not be sent")
                except ValueError:
                    pass
            assert hit == [], "nothing may reach the network so far"
            await c.set_name("  Garden Fountain  ")

    asyncio.run(go())
    assert len(hit) == 1
    body = __import__("json").loads(hit[0].content)
    assert body["method"] == "Sys.SetConfig" and body["params"] == _rename_params("Garden Fountain")


def test_collector_rename():
    fleet = make_fleet()
    with tempfile.TemporaryDirectory() as tmp:
        store, col, _ = run_collector(fleet, tmp, shelly_password="secret-pw")
        dev_id = "shellyplus1-441793000301"
        fake = fleet.devices["192.168.10.41"]
        before = len(store.snapshots(dev_id))

        res = asyncio.run(col.rename(dev_id, " Carport Wall Switch "))
        assert res == {"ok": True, "changed": True, "old": "Bedroom Wall Switch", "new": "Carport Wall Switch",
                       "restart_required": False}
        assert fake.config["sys"]["device"]["name"] == "Carport Wall Switch"
        assert fake.writes == [_rename_params("Carport Wall Switch")], "exactly one write, exactly this form"
        assert store.device(dev_id)["name"] == "Carport Wall Switch"
        assert len(store.snapshots(dev_id)) == before + 1, "new name lands in the backup history"
        log = store.audit(dev_id)
        assert log[0]["ok"] and log[0]["old"] == "Bedroom Wall Switch" and log[0]["new"] == "Carport Wall Switch"

        # Same name: no write access
        assert asyncio.run(col.rename(dev_id, "Carport Wall Switch"))["changed"] is False
        assert len(fake.writes) == 1

        # Invalid name: error before any network access
        calls = len(fake.calls)
        try:
            asyncio.run(col.rename(dev_id, "x" * 80))
            raise AssertionError("ValueError expected")
        except ValueError:
            pass
        assert len(fake.calls) == calls

        # DHCP swapped addresses: a different device answers at the stored IP -> do not write
        d = fleet.devices
        d["192.168.10.22"], d["192.168.10.23"] = d["192.168.10.23"], d["192.168.10.22"]
        other = d["192.168.10.22"]
        try:
            asyncio.run(col.rename("shellyplusi4-b8d61a000102", "Hall Wall Switch"))
            raise AssertionError("RenameError expected")
        except RenameError as exc:
            assert "answers as" in str(exc)
        assert other.writes == [] and d["192.168.10.23"].writes == []
        assert store.audit("shellyplusi4-b8d61a000102")[0]["ok"] is False

        # Device does not take the name: report no success
        g4 = fleet.devices["192.168.10.31"]
        g4.ignore_name_writes = True
        try:
            asyncio.run(col.rename("shelly2pmg4-1020ba000201", "Study Shutter"))
            raise AssertionError("RenameError expected")
        except RenameError as exc:
            assert "did not confirm" in str(exc)
        assert store.device("shelly2pmg4-1020ba000201")["name"] == "Home Office Shutter"

        bad = [c for c in fleet.all_calls() if not is_read_only(c) and c != "Sys.SetConfig"]
        assert not bad, bad
        writes = [w for dev in fleet.devices.values() for w in dev.writes]
        assert all(is_rename_call("Sys.SetConfig", w) for w in writes)


# ---- Scan and read -----------------------------------------------------------------------

def test_scan_finds_only_gen2plus():
    fleet = make_fleet()

    async def go():
        async with httpx.AsyncClient(transport=fleet.transport()) as http:
            return await discovery.scan(http, ["192.168.10.0/24"], [], 32, 0.5)

    found = asyncio.run(go())
    assert "192.168.10.99" not in found, "Gen1 must be ignored"
    assert set(found) == {"192.168.10.21", "192.168.10.22", "192.168.10.23", "192.168.10.31",
                          "192.168.10.32", "192.168.10.41", "192.168.10.51"}
    assert discovery.expand_targets(["10.0.0.0/8"], []) == [], "networks that are too large are not scanned"
    assert discovery.expand_targets(["192.168.10.0/30"], ["192.168.7.5"]) == ["192.168.10.1", "192.168.10.2", "192.168.7.5"]


def test_plan_targets():
    plan = discovery.plan_targets(["192.168.0.0/16"])
    assert plan.total == 65534 and not plan.warnings, "/16 is allowed"
    plan = discovery.plan_targets(["192.168.0.0/15", "broken", "fd00::/64", "192.168.10.0/24"])
    assert plan.total == 254
    assert len(plan.warnings) == 3 and "/16" in plan.warnings[0] and "131,072" in plan.warnings[0]
    # Overlaps and duplicate single addresses count only once
    plan = discovery.plan_targets(["192.168.10.0/24", "192.168.10.128/25", "192.168.10.0/24"],
                                  ["192.168.10.5", "192.168.20.7", "192.168.20.7", "shelly-guest.example.com"])
    assert plan.total == 254 + 2
    hosts = list(plan)
    assert len(hosts) == len(set(hosts)) == plan.total
    assert hosts[-2:] == ["192.168.20.7", "shelly-guest.example.com"]


def test_scan_progress_and_cancel():
    fleet = make_fleet()
    seen_progress: list[tuple[int, int, int]] = []
    stored: list[str] = []

    async def full():
        async with httpx.AsyncClient(transport=fleet.transport()) as http:
            return await discovery.scan(
                http, ["192.168.10.0/24"], [], 16, 0.5,
                progress=lambda d, t, f: seen_progress.append((d, t, f)),
                on_found=lambda h, i: stored.append(h),
            )

    found = asyncio.run(full())
    assert seen_progress[0] == (0, 254, 0) and seen_progress[-1] == (254, 254, 7)
    assert [d for d, _, _ in seen_progress] == sorted(d for d, _, _ in seen_progress), "monoton"
    assert sorted(stored) == sorted(found)

    last = {"done": 0}

    async def cancelled():
        ev = asyncio.Event()

        def prog(d, t, f):
            last["done"] = d
            if d >= 40:
                ev.set()
        async with httpx.AsyncClient(transport=fleet.transport()) as http:
            return await discovery.scan(http, ["192.168.10.0/24"], [], 8, 0.5, progress=prog, cancel=ev)

    res = asyncio.run(cancelled())
    # Cancel at 40: at most the 8 addresses that were in progress still finish
    assert 40 <= last["done"] <= 48, last
    assert {"192.168.10.21", "192.168.10.22", "192.168.10.23", "192.168.10.31", "192.168.10.32"} <= set(res)
    assert "192.168.10.51" not in res, "no new addresses after cancelling"


def test_scan_16_finds_renumbered_device():
    fleet = make_fleet()
    moved = fleet.devices.pop("192.168.10.21")
    fleet.devices["192.168.42.7"] = moved
    with tempfile.TemporaryDirectory() as tmp:
        store, col, summary = run_collector(fleet, tmp, scan=True, scan_networks=["192.168.0.0/16"])
        assert summary["scanned"] and summary["found"] == 7 and not summary["cancelled"]
        assert col.plan.total == 65534
        dev = next(d for d in store.devices() if d["id"] == moved.id)
        assert dev["host"] == "192.168.42.7"


def test_no_auto_scan_when_disabled():
    fleet = make_fleet()
    with tempfile.TemporaryDirectory() as tmp:
        s = make_settings(tmp, scan_every_n_polls=0)
        store = Store(s.db_path)
        col = Collector(store, s, transport=fleet.transport())
        summary = asyncio.run(col.run(scan=False, auto_scan=False))
        assert summary["scanned"] is False and store.devices() == []
        summary = asyncio.run(col.run(scan=True))
        assert summary["found"] == 7, "the \"Scan network\" button scans anyway"


def test_full_run_and_snapshots():
    fleet = make_fleet()
    with tempfile.TemporaryDirectory() as tmp:
        store, col, summary = run_collector(fleet, tmp)
        assert summary["found"] == 7 and summary["polled"] == 7
        assert summary["errors"] == 1, "a device with password must fail without SHELLY_PASSWORD"
        locked = store.device("shelly1minig3-84fce6000401")
        assert "password" in (locked["last_error"] or "")

        g4a = store.device("shelly2pmg4-1020ba000201")
        assert g4a["profile"] == "cover" and g4a["name"] == "Home Office Shutter"
        snap = store.latest_snapshot(g4a["id"])
        script = snap["data"]["scripts"][0]
        assert script["code"] == "// frost guard\nlet t = 0;\nfunction tick() { t++; }\n", "script code read in pages"
        assert snap["data"]["kvs"] == {"a": 1, "b": "two", "c": [3]}, "KVS over several pages (list format)"
        assert store.latest_snapshot("shelly2pmg4-58e6c5000202")["data"]["kvs"] == {"x": True}, "KVS Dict-Format"
        assert "status" not in snap["data"], "runtime status does not belong in the backup"

        # Second run without change: no new snapshot
        summary2 = asyncio.run(col.run())
        assert summary2["changed"] == 0
        assert len(store.snapshots(g4a["id"])) == 1

        # Only cfg_rev and firmware change: does not count as a configuration change
        dev = fleet.devices["192.168.10.31"]
        dev.config["sys"]["cfg_rev"] = 8
        dev.ver = "2.1.2"
        asyncio.run(col.run())
        assert len(store.snapshots(g4a["id"])) == 1
        assert store.device(g4a["id"])["ver"] == "2.1.2"

        # Real change: new snapshot, the diff shows exactly this path
        dev.config["cover:0"]["power_limit"] = 250
        asyncio.run(col.run())
        snaps = store.snapshots(g4a["id"])
        assert len(snaps) == 2
        new, old = store.snapshot(snaps[0]["id"]), store.snapshot(snaps[1]["id"])
        changes = compare.diff_flat(compare.backup_flat(old["data"]), compare.backup_flat(new["data"]))
        assert [c["path"] for c in changes] == ["config.cover:0.power_limit"], changes
        assert changes[0]["old"] == 300 and changes[0]["new"] == 250

        # Retention: at most snapshots_keep (5)
        for i in range(8):
            dev.config["cover:0"]["power_limit"] = 200 + i
            asyncio.run(col.run())
        assert len(store.snapshots(g4a["id"])) == 5


def test_password_works_when_set():
    fleet = make_fleet()
    with tempfile.TemporaryDirectory() as tmp:
        store, _, summary = run_collector(fleet, tmp, shelly_password="secret-pw")
        assert summary["errors"] == 0
        assert store.device("shelly1minig3-84fce6000401")["auth_en"] is True


def test_dhcp_reassignment_is_detected():
    fleet = make_fleet()
    with tempfile.TemporaryDirectory() as tmp:
        store, col, _ = run_collector(fleet, tmp)
        # Two devices swap their addresses
        d = fleet.devices
        d["192.168.10.22"], d["192.168.10.23"] = d["192.168.10.23"], d["192.168.10.22"]
        asyncio.run(col.run(scan=False))
        b = store.device("shellyplusi4-b8d61a000102")
        assert "now answers as" in (b["last_error"] or "")
        assert b["host"] == "192.168.10.23", "the address is corrected via the counterpart"
        asyncio.run(col.run(scan=True))
        assert store.device("shellyplusi4-b8d61a000102")["host"] == "192.168.10.23"
        assert store.device("shellyplusi4-b8d61a000103")["host"] == "192.168.10.22"
        assert store.device("shellyplusi4-b8d61a000103")["last_error"] is None
        # The configuration was never stored swapped
        snap = store.latest_snapshot("shellyplusi4-b8d61a000102")
        assert snap["data"]["config"]["sys"]["device"]["name"] == "Wall Switch B"


def test_device_id_change_keeps_history():
    """Firmware 2.x renames devices: same MAC, new ID = the same device."""
    old_id = "shellyplusi4-b8d61a000102"
    new_id = "shellyi4plus-b8d61a000102"

    def rename(fleet):
        fake = next(f for f in fleet.devices.values() if f.id == old_id)
        fake.id = new_id

    # a) Read without scan: the entry moves, snapshots and policy stay
    fleet = make_fleet()
    with tempfile.TemporaryDirectory() as tmp:
        store, col, _ = run_collector(fleet, tmp)
        n = len(store.devices())
        before = [s["id"] for s in store.snapshots(old_id)]
        assert before
        pol.write_text(col.settings.policy_path, f"overrides:\n  {old_id}:\n    ignore: [\"sys.device.name\"]\n")
        rename(fleet)
        asyncio.run(col.run(scan=False))
        assert store.device(old_id) is None and len(store.devices()) == n
        dev = store.device(new_id)
        assert dev["last_error"] is None and dev["host"]
        assert set(before) <= {s["id"] for s in store.snapshots(new_id)}
        assert store.renamed_to(old_id) == new_id
        text = pol.read_text(col.settings.policy_path)
        assert new_id in text and old_id not in text

    # b) The scan already created the new ID as a device of its own: both entries merge
    fleet = make_fleet()
    with tempfile.TemporaryDirectory() as tmp:
        store, col, _ = run_collector(fleet, tmp)
        n = len(store.devices())
        host = store.device(old_id)["host"]
        first = store.device(old_id)["first_seen"]
        rename(fleet)
        info = fleet.devices[host].device_info()
        store.db.execute("INSERT INTO devices (id, host, mac, first_seen) VALUES (?,?,?,?)",
                         (new_id, host, info["mac"], "2099-01-01T00:00:00+00:00"))
        store.db.commit()
        asyncio.run(col.run(scan=True))
        assert store.device(old_id) is None and len(store.devices()) == n
        assert store.device(new_id)["first_seen"] == first
        assert len(store.snapshots(new_id)) >= 2


# ---- Policy ---------------------------------------------------------------------------------

POLICY = """
firmware:
  require_latest_stable: true
  same_version_per_type: true
security:
  require_auth: true
baseline:
  sys.location.tz: Europe/Berlin
  wifi.ap.enable: false
  sys.device.does_not_exist: 1
  cover:*.power_limit: {max: 280}
profiles:
  - name: Rollladen
    match: {profile: cover}
    settings:
      cover:*.obstruction_detection.enable: true
      cover:*.in_mode: {in: [dual, detached]}
  - name: I4
    match: {app: [PlusI4]}
    ignore: [sys.device.does_not_exist]
    settings:
      input:*.type: button
overrides:
  shelly2pmg4-58e6c5000202:
    ignore: [cover:*.obstruction_detection.enable]
    settings:
      cover:0.power_limit: 300
"""


def test_policy_evaluation():
    fleet = make_fleet()
    p = pol.parse(POLICY)
    with tempfile.TemporaryDirectory() as tmp:
        store, _, _ = run_collector(fleet, tmp, shelly_password="secret-pw")
        from app import report
        rep = report.build(store, p, 15)
    by_dev: dict[str, list] = {}
    for f in rep["findings"]:
        by_dev.setdefault(f["device_id"], []).append((f["kind"], f["path"], f["source"]))

    plus1 = by_dev["shellyplus1-441793000301"]
    assert ("mismatch", "sys.location.tz", "Baseline") in plus1
    assert ("mismatch", "wifi.ap.enable", "Baseline") in plus1
    # The path exists on no device: outdated rule instead of a finding per device
    assert not any(p == "sys.device.does_not_exist" for _, p, _ in plus1)
    assert any(s["path"] == "sys.device.does_not_exist" and s["source"] == "Baseline" for s in rep["stale_rules"])
    assert ("firmware", "firmware.stable", "Firmware") not in plus1
    assert ("security", "auth", "Security") in plus1
    assert not any(p.startswith("cover") for _, p, _ in plus1), "cover rules do not apply to a Plus 1"

    g4a = by_dev["shelly2pmg4-1020ba000201"]
    assert ("mismatch", "cover:0.power_limit", "Baseline") in g4a, "max 280 violated (300)"
    assert not any(p == "cover:0.obstruction_detection.enable" for _, p, _ in g4a)
    assert not any(p == "cover:0.in_mode" for _, p, _ in g4a), "'in' satisfied"

    g4b = by_dev["shelly2pmg4-58e6c5000202"]
    assert not any("obstruction" in p for _, p, _ in g4b), "device override ignores the rule"
    assert not any(p == "cover:0.power_limit" for _, p, _ in g4b), "override replaces max 280 with 300"

    i4a = by_dev["shellyplusi4-b8d61a000101"]
    assert ("firmware", "firmware.stable", "Firmware") in i4a
    assert ("firmware", "firmware.peer", "Firmware") not in i4a, "the stable message covers the peer comparison"
    assert not any(p == "sys.device.does_not_exist" for _, p, _ in i4a), "Profil-ignore entfernt Basisregel"
    i4b = by_dev["shellyplusi4-b8d61a000102"]
    assert ("mismatch", "input:1.type", "Profile: I4") in i4b
    assert not any(k == "firmware" for k, _, _ in i4b)

    assert "shelly1minig3-84fce6000401" not in by_dev or \
        ("security", "auth", "Security") not in by_dev["shelly1minig3-84fce6000401"]

    # Without a reported stable update the peer comparison applies alone
    fleet2 = make_fleet()
    fleet2.devices["192.168.10.21"].stable = None
    with tempfile.TemporaryDirectory() as tmp:
        store2, _, _ = run_collector(fleet2, tmp, shelly_password="secret-pw")
        from app import report
        rep2 = report.build(store2, p, 15)
    i4a2 = {(f["kind"], f["path"]) for f in rep2["findings"] if f["device_id"] == "shellyplusi4-b8d61a000101"}
    assert ("firmware", "firmware.peer") in i4a2 and ("firmware", "firmware.stable") not in i4a2



def test_default_profiles_and_calibration():
    """Profiles "Shutter" and "Button input" from the default policy, calibration from the status."""
    from app import devicepage
    fleet = make_fleet()
    fleet.devices["192.168.10.32"].cover_status["cover:0"] = {"pos_control": False, "errors": ["obstruction"],
                                                               "state": "closing", "apower": 141.5}
    default = pol.parse(open(pol.DEFAULT_POLICY_PATH, encoding="utf-8").read())
    assert default["cover"] == {"require_calibration": True}
    with tempfile.TemporaryDirectory() as tmp:
        store, _, _ = run_collector(fleet, tmp, shelly_password="secret-pw")
        from app import report
        rep = report.build(store, default, 15)
        g4b_status = store.device("shelly2pmg4-58e6c5000202")["status"]
        g4b_config = store.latest_snapshot("shelly2pmg4-58e6c5000202")["data"]["config"]
    by_dev: dict[str, set] = {}
    for f in rep["findings"]:
        by_dev.setdefault(f["device_id"], set()).add((f["kind"], f["path"], f["source"]))
    rows = {r["id"]: r for r in rep["devices"]}

    assert rows["shelly2pmg4-1020ba000201"]["profiles"] == ["Shutter"]
    assert rows["shellyplusi4-b8d61a000102"]["profiles"] == ["Button input"]
    assert rows["shellyplus1-441793000301"]["profiles"] == []

    g4a = by_dev.get("shelly2pmg4-1020ba000201", set())
    assert not any(p.startswith("cover:") for _, p, _ in g4a), g4a      # satisfies the profile, calibrated
    g4b = by_dev["shelly2pmg4-58e6c5000202"]
    assert ("mismatch", "cover:0.obstruction_detection.enable", "Profile: Shutter") in g4b
    assert ("calibration", "cover:0.pos_control", "Calibration") in g4b

    i4b = by_dev["shellyplusi4-b8d61a000102"]
    assert ("mismatch", "input:1.type", "Profile: Button input") in i4b
    assert ("mismatch", "input:0.factory_reset", "Profile: Button input") in i4b
    assert not any(k == "missing" for k, _, _ in i4b), i4b

    # Without require_calibration no message
    off = pol.parse("cover: {require_calibration: false}")
    assert not [f for f in pol.evaluate(off, {"id": "x"}, {}, g4b_status, {}) if f["kind"] == "calibration"]
    assert pol.validate({"cover": [1]}) == ["cover: must be a mapping"]

    # Shutter card on the device page
    cv = devicepage.cover_view(g4b_config, g4b_status)
    assert len(cv) == 1 and cv[0]["id"] == "cover:0"
    c = cv[0]
    assert c["calibrated"] is False and c["moving"] is True and c["apower"] == 141.5
    assert c["errors"] == ["obstruction"] and c["temperature"] == 38.2
    assert c["limits"]["power_limit"] == 300 and c["limits"]["power_thr"] == 160 and c["limits"]["holdoff"] == 1
    assert c["obstruction"] == {"enable": False, "direction": "both", "action": "stop"}
    assert devicepage.cover_view({"sys": {}}, {"sys": {}}) == []
    assert devicepage.cover_view(None, None) == []


EXAMPLE_NETWORK = """
network:
  subnet: 192.168.200.0/24
  dhcp_pool: 192.168.7.0/24
  blocks:
    - {name: 1 Mini Gen3, match: {id: "shelly1minig3-*"}, range: 192.168.200.1-9}
    - {name: Plus 1, match: {id: "shellyplus1-*"}, range: 192.168.200.10-19}
    - {name: Plus i4, match: {id: "shellyplusi4-*"}, range: 192.168.200.30-39}
    - {name: 2PM Gen4, match: {id: "shelly2pmg4-*"}, range: 192.168.200.100-129}
"""


def _dev(dev_id, host, app="x"):
    return {"id": dev_id, "host": host, "app": app}


def test_network_plan():
    p = pol.parse(EXAMPLE_NETWORK)
    used = {"192.168.200.1", "192.168.200.3", "192.168.200.10", "192.168.200.11", "192.168.7.68"}

    ok = pol.network_findings(p, _dev("shelly1minig3-84fce6000401", "192.168.200.1"), used)
    assert ok == [], "right block, no message"

    new = pol.network_findings(p, _dev("shelly1minig3-84fce638ff80", "192.168.7.68"), used)
    assert len(new) == 1 and new[0]["tag"] == "pool" and new[0]["kind"] == "network"
    assert "Next free address in block: 192.168.200.2." in new[0]["note"], "gap .2 is suggested"
    assert new[0]["expected"] == "192.168.200.1-9 (1 Mini Gen3)" and new[0]["actual"] == "192.168.7.68"

    wrong = pol.network_findings(p, _dev("shellyplusi4-b8d61a000101", "192.168.200.12"), used)
    assert [f["tag"] for f in wrong] == ["block"] and "192.168.200.30" in wrong[0]["note"]

    outside = pol.network_findings(p, _dev("shellyplus1-441793000301", "192.168.10.41"), used)
    assert [f["tag"] for f in outside] == ["outside"] and "192.168.200.12" in outside[0]["note"]

    unknown = pol.network_findings(p, _dev("shellyplusplugs-80646fcefe04", "192.168.200.40", "PlusPlugS"), used)
    assert [f["tag"] for f in unknown] == ["noblock"], "without a matching block: a hint instead of a silent gap"
    assert "PlusPlugS" in unknown[0]["actual"]

    assert pol.network_findings(p, _dev("shellyplus1-x", "shelly-guest.example.com"), used) == [], "host name: cannot be checked"
    assert pol.network_findings(pol.parse("baseline: {}"), _dev("shellyplus1-x", "1.2.3.4"), used) == [], "without network"

    # Full blocks and reservation: two new devices get different suggestions
    small = pol.parse("network:\n  dhcp_pool: 192.168.7.0/24\n  blocks:\n"
                      "    - {name: M, match: {id: 'm-*'}, range: 192.168.200.1-2}\n")
    pool = {"192.168.200.1"}
    a = pol.network_findings(small, _dev("m-a", "192.168.7.10"), pool, reserve=True)
    b = pol.network_findings(small, _dev("m-b", "192.168.7.11"), pool, reserve=True)
    assert "192.168.200.2" in a[0]["note"] and "is full" in b[0]["note"]

    assert pol.parse_range("192.168.200.100-129") == (pol.ipaddress.ip_address("192.168.200.100"),
                                                     pol.ipaddress.ip_address("192.168.200.129"))
    assert pol.parse_range("192.168.200.16/30")[1] == pol.ipaddress.ip_address("192.168.200.18")
    assert str(pol.parse_range("192.168.200.5")[0]) == "192.168.200.5"


def test_network_validation():
    errs = pol.validate({"network": {"subnet": "192.168.200.0/24", "pool": 1, "blocks": [
        {"name": "a", "match": {"colour": "x"}, "range": "192.168.200.10-19"},
        {"name": "b", "match": {"id": "x"}, "range": "192.168.201.1-9"},
        {"name": "c", "match": {"id": "x"}, "range": "192.168.200.20-10"},
        {"name": "d", "match": {"id": "x"}},
        {"name": "e", "range": "192.168.200.40-49"},
    ]}})
    joined = " | ".join(errs)
    assert "unknown field 'pool'" in joined
    assert "blocks[0].match: unknown field 'colour'" in joined
    assert "blocks[1].range: 192.168.201.1-9 is not fully inside network.subnet" in joined
    assert "blocks[2].range" in joined and "end is before start" in joined
    assert "blocks[3]: 'range' is missing" in joined
    assert "blocks[4]: 'match' is missing" in joined
    assert pol.validate({"network": {"subnet": "not a network"}})[0].startswith("network.subnet:")
    assert pol.validate(pol.parse(EXAMPLE_NETWORK) | {"peer_ignore": []}) == []


def test_naming_plan():
    errs = pol.validate({"names": {"a": "", "b": "x" * 70, "c": "ok"}})
    assert len(errs) == 2 and errs[0].startswith("names.a:") and "longer than 64" in errs[1]
    assert pol.validate({"names": ["x"]}) == ["names: must be a mapping 'device-id: name'"]
    p = pol.parse("names:\n  shellyplus1-441793000301: '  Carport Wall Switch '\n"
                  "  shelly2pmg4-1020ba000201: Home Office Shutter\n  shellyplusi4-typo: X\n")
    assert p["names"]["shellyplus1-441793000301"] == "Carport Wall Switch", "trimmed like on write"
    fleet = make_fleet()
    with tempfile.TemporaryDirectory() as tmp:
        store, _, _ = run_collector(fleet, tmp, shelly_password="secret-pw")
        from app import report
        rep = report.build(store, p, 15)
    names = [f for f in rep["findings"] if f["kind"] == "name"]
    assert [(f["device_id"], f["expected"], f["actual"]) for f in names] == [
        ("shellyplus1-441793000301", "Carport Wall Switch", "Bedroom Wall Switch")], "matching name: no finding"
    assert names[0]["source"] == "Naming plan"
    assert rep["names_unknown"] == ["shellyplusi4-typo"]
    rows = {d["id"]: d for d in rep["devices"]}
    assert rows["shellyplus1-441793000301"]["planned_name"] == "Carport Wall Switch"
    assert rows["shellyplusi4-b8d61a000101"]["planned_name"] is None


def test_policy_validation():
    errs = pol.validate({"baseline": [], "profiles": [{"match": {"colour": "x"}}], "foo": 1})
    joined = " | ".join(errs)
    assert "Unknown section 'foo'" in joined
    assert "baseline: must be a mapping" in joined
    assert "'name' is missing" in joined
    assert "unknown field 'colour'" in joined
    try:
        pol.parse("baseline: [unclosed")
        raise AssertionError("YAML error expected")
    except pol.PolicyError as exc:
        assert exc.errors[0].startswith("YAML error")
    default = pol.parse(open(pol.DEFAULT_POLICY_PATH, encoding="utf-8").read())
    assert default["baseline"]["sys.location.tz"] == "Europe/Berlin"


def test_peer_groups():
    fleet = make_fleet()
    with tempfile.TemporaryDirectory() as tmp:
        store, _, _ = run_collector(fleet, tmp, shelly_password="secret-pw")
        from app import report
        default = pol.parse(open(pol.DEFAULT_POLICY_PATH, encoding="utf-8").read())
        rep = report.build(store, default, 15)
        groups = compare.peer_groups(store.devices(), rep["configs"], default["peer_ignore"])
    g = {(x["app"], x["profile"]): x for x in groups}
    covers = g[("S2PMG4", "cover")]
    paths = {d["path"]: d for d in covers["diffs"]}
    assert "cover:0.obstruction_detection.enable" in paths
    assert "cover:0.maxtime_open" not in paths, "calibration values are in peer_ignore"
    assert "sys.device.name" not in paths and "wifi.ap.ssid" not in paths and "mqtt.client_id" not in paths
    assert not paths["cover:0.obstruction_detection.enable"]["has_majority"], "1:1 has no majority"

    i4 = g[("PlusI4", "")]
    d = {x["path"]: x for x in i4["diffs"]}
    assert set(d) == {"input:1.type"}, d.keys()
    assert d["input:1.type"]["majority"] == "button"
    assert d["input:1.type"]["outliers"] == ["shellyplusi4-b8d61a000102"]
    assert i4["versions"] == ["1.7.5", "1.7.6"]
    assert g[("Plus1", "")]["diffs"] == [], "a single device has no comparison"


def test_peer_device_ignore():
    """ignore from profile/override also applies in the peer comparison (replaces peer_variants)."""
    assert pol.validate({"peer_variants": ["cover:*.swap_inputs"]}) == [], "the old key stays allowed"
    fleet = make_fleet()
    fleet.devices["192.168.10.32"].config["cover:0"]["obstruction_detection"]["enable"] = True  # otherwise equal
    with tempfile.TemporaryDirectory() as tmp:
        store, _, _ = run_collector(fleet, tmp, shelly_password="secret-pw")
        from app import report
        default = pol.parse(open(pol.DEFAULT_POLICY_PATH, encoding="utf-8").read())
        rep = report.build(store, default, 15)
        devs = store.devices()
        dig = {d["id"]: pol.device_ignore(default, d) for d in devs}
        assert "cover:*.swap_inputs" in dig["shelly2pmg4-58e6c5000202"]
        covers = lambda di: next(x for x in compare.peer_groups(  # noqa: E731
            devs, rep["configs"], default["peer_ignore"], default["peer_depends"], di) if x["profile"] == "cover")
        assert covers(dig)["diffs"] == [], "swap_inputs free via profile: uniform"
        assert "variants" not in covers(dig)
        assert [d["path"] for d in covers({})["diffs"]] == ["cover:0.swap_inputs"], "without ignore one difference"


def _peer(fleet, app, depends=None, ignore=None):
    with tempfile.TemporaryDirectory() as tmp:
        store, _, _ = run_collector(fleet, tmp, shelly_password="secret-pw")
        from app import report
        default = pol.parse(open(pol.DEFAULT_POLICY_PATH, encoding="utf-8").read())
        rep = report.build(store, default, 15)
        groups = compare.peer_groups(store.devices(), rep["configs"],
                                     default["peer_ignore"] if ignore is None else ignore,
                                     default["peer_depends"] if depends is None else depends)
    return next(g for g in groups if g["app"] == app)


def test_peer_depends():
    """Differences without effect or in read-only values do not count, but are listed under hidden."""
    fleet = make_fleet()
    i4 = [fleet.devices[h] for h in ("192.168.10.21", "192.168.10.22", "192.168.10.23")]
    for n, dev in enumerate(i4):
        dev.config["cloud"] = {"enable": False, "server": f"shelly-{n}.cloud"}
    g = _peer(fleet, "PlusI4")
    assert [d["path"] for d in g["diffs"]] == ["input:1.type"], "cloud.server without cloud does not count"
    hid = {h["path"]: h for h in g["hidden"]}
    assert hid["cloud.server"]["reason"] == "no_effect" and "cloud.enable = true" in hid["cloud.server"]["note"]
    assert set(hid["cloud.server"]["values"].values()) == {"shelly-0.cloud", "shelly-1.cloud", "shelly-2.cloud"}
    assert sum(len(x["diffs"]) for x in [g]) == 1, "counter: only visible differences"

    i4[1].config["cloud"]["enable"] = True                      # one device with cloud on: difference visible
    g = _peer(fleet, "PlusI4")
    assert {d["path"] for d in g["diffs"]} == {"cloud.enable", "cloud.server", "input:1.type"}
    assert not [h for h in g["hidden"] if h["path"] == "cloud.server"]

    # Condition per component: input:1 off -> input:1.type does not count, input:0 stays untouched
    fleet = make_fleet()
    for h in ("192.168.10.21", "192.168.10.22", "192.168.10.23"):
        fleet.devices[h].config["input:1"]["enable"] = False
    g = _peer(fleet, "PlusI4")
    assert g["diffs"] == [] and [h["path"] for h in g["hidden"]] == ["input:1.type"]
    assert "input:1.enable = true" in g["hidden"][0]["note"]
    # Without the control field (other firmware) nothing can be ruled out: counts
    assert [d["path"] for d in _peer(make_fleet(), "PlusI4")["diffs"]] == ["input:1.type"]
    # peer_depends: [] schaltet es ab
    assert [d["path"] for d in _peer(fleet, "PlusI4", depends=[])["diffs"]] == ["input:1.type"]

    # Read-only and managed by the device: never counted, even without peer_ignore
    fleet = make_fleet()
    fleet.devices["192.168.10.21"].config["sys"]["device"]["enhanced_security"] = True
    fleet.devices["192.168.10.22"].config["sys"]["ui_data"] = {"consumption_types": ["light"]}
    g = _peer(fleet, "PlusI4", ignore=["sys.device.name", "*:*.name", "wifi.ap.ssid", "mqtt.client_id"])
    hid = {h["path"]: h["reason"] for h in g["hidden"]}
    assert hid == {"sys.device.enhanced_security": "read_only", "sys.ui_data.consumption_types": "managed"}, hid
    assert [d["path"] for d in g["diffs"]] == ["input:1.type"]


def test_peer_depends_helpers_and_validation():
    assert compare.bind_wildcards("cover:*.obstruction_detection.enable", "cover:1.obstruction_detection.action") \
        == "cover:1.obstruction_detection.enable"
    assert compare.bind_wildcards("mqtt.enable", "mqtt.server") == "mqtt.enable"
    assert compare.cond_ok(True, True) and not compare.cond_ok(True, False)
    assert compare.cond_ok({"not": "detached"}, "dual") and not compare.cond_ok({"not": "detached"}, "detached")
    assert compare.cond_ok({"in": ["switch", "button"]}, "button") and not compare.cond_ok({"in": ["switch"]}, "analog")

    default = pol.parse(open(pol.DEFAULT_POLICY_PATH, encoding="utf-8").read())
    assert default["peer_depends"] == pol.DEFAULT_PEER_DEPENDS, "default_policy.yaml and code are equal"
    assert pol.parse("baseline: {}")["peer_depends"] == pol.DEFAULT_PEER_DEPENDS, "default without its own key"
    assert pol.parse("peer_depends: []")["peer_depends"] == []
    errs = " | ".join(pol.validate({"peer_depends": [
        {"paths": ["cloud.server"], "when": {"cloud.enable": True}, "if": 1},
        {"paths": [], "when": {"x": 1}},
        {"paths": ["mqtt..server"], "when": {"mqtt.enable": True}},
        {"paths": ["input:[0.type"], "when": {"input:*.enable": True}},
        {"paths": ["a.b"], "when": {"a.c": {"max": 3}}},
        {"paths": ["a.b"], "when": {"a.c": {"in": "x"}}},
        {"paths": ["a.b"]},
        "x",
    ]}))
    for want in ("peer_depends[0]: unknown field 'if'", "peer_depends[1].paths: must be a non-empty list",
                 "has an empty level", "unclosed '['", "peer_depends[4].when.a.c: only a value",
                 "'in' needs a list", "peer_depends[6].when: must be a mapping", "peer_depends[7]: must be a mapping"):
        assert want in errs, (want, errs)
    assert pol.validate({"peer_depends": "x"}) == ["peer_depends: must be a list of rules {paths, except, when}"]


def test_device_settings_view():
    """Schema per component, values without effect, read-only fields, policy target and type majority."""
    from app import devicepage as dp
    cfg = {"sys": {"device": {"name": "X", "mac": "AA", "fw_id": "f"}, "cfg_rev": 3, "ui_data": {"k": 1}},
           "cloud": {"enable": False, "server": "s"},
           "cover:0": {"id": 0, "name": "Motor", "power_limit": 280, "maxtime_open": 25.0, "new_field": "x",
                       "obstruction_detection": {"enable": True, "direction": "both"}},
           "zigbee": {"enable": False}}
    view = dp.settings_view(cfg, concrete={"cover:0.power_limit": ({"max": 250}, "Baseline")},
                            depends=pol.DEFAULT_PEER_DEPENDS,
                            outliers={"cover:0.name": {"majority": None, "has_majority": False}})
    secs = {s["id"]: s for s in view["sections"]}
    assert [s["id"] for s in view["sections"]] == ["sys", "cloud", "cover:0", "zigbee"], "feste Reihenfolge"
    assert secs["cover:0"]["title"] == "Cover 0 · Motor" and secs["zigbee"]["title"] == "Zigbee"
    f = {x["path"]: x for x in secs["cover:0"]["fields"]}
    assert [x["path"] for x in secs["cover:0"]["fields"]][:2] == ["cover:0.name", "cover:0.power_limit"], "Schema zuerst"
    assert secs["cover:0"]["fields"][-1]["path"] == "cover:0.new_field" and f["cover:0.new_field"]["known"] is False
    assert f["cover:0.power_limit"]["policy"] == {"expected": "<= 250", "source": "Baseline", "ok": False,
                                                  "value": 250}, "\"Use 250\" for a pure upper limit"
    assert f["cover:0.power_limit"]["editable"] is True and f["cover:0.new_field"]["editable"] is False
    assert f["cover:0.obstruction_detection.enable"]["editable"] is True
    sysf = {x["path"]: x for x in secs["sys"]["fields"]}
    assert sysf["sys.device.name"]["editable"] is True, "name like any other setting"
    assert dp.use_value({"max": 3}) == 3 and dp.use_value({"min": 1, "max": 3}) is None
    assert dp.use_value({"in": [1]}) is None and dp.use_value("stop") == "stop" and dp.use_value([1]) is None
    assert f["cover:0.name"]["peer"]["has_majority"] is False
    c = {x["path"]: x for x in secs["cloud"]["fields"]}
    assert c["cloud.server"]["active"] is False and c["cloud.server"]["needs"] == "cloud.enable = true"
    assert secs["cloud"]["enabled"] is False
    info = {x["path"]: x["reason"] for x in view["info"]}
    assert info == {"sys.device.mac": "read_only", "sys.device.fw_id": "read_only", "sys.cfg_rev": "read_only",
                    "sys.ui_data.k": "managed", "cover:0.id": "read_only", "cover:0.maxtime_open": "managed"}, info
    assert dp.field_meta("wifi.sta1.ipv4mode", "dhcp")["label"] == "Fallback: IPv4 mode"
    assert dp.field_meta("foo:3.bar_baz.qux", [1])["kind"] == "object"
    assert dp.field_meta("foo:3.bar_baz.qux", 1)["label"] == "Bar baz › qux"
    assert dp.settings_view(None) == {"sections": [], "info": []}
    # Readable names for findings and the peer comparison; synthetic paths have their own names
    assert dp.setting_label("switch:0.auto_off") == "Switch 0 · Auto off"
    assert dp.setting_label("firmware.peer") == "Firmware behind its type" and dp.setting_label("auth") == "Password protection"
    assert dp.setting_label("cover:1.pos_control") == "Cover 1 · Calibration"
    assert dp.setting_label("foo:2.bar_baz.qux") == "Foo 2 · Bar baz › qux", "unknown fields keep the fallback"

    groups = [{"app": "A", "profile": "", "model_name": "M", "devices": [{"id": "a"}, {"id": "b", "name": "B"}, {"id": "c"}],
               "diffs": [{"path": "p", "values": {"a": 1, "b": 2, "c": 2}, "majority": 2, "has_majority": True,
                          "outliers": ["a"]}]}]
    pv = dp.peer_view(groups, "a")
    assert pv["diffs"] == [{"path": "p", "label": "P", "value": 1, "majority": 2, "has_majority": True,
                            "others": [{"id": "b", "name": "B", "value": 2}, {"id": "c", "name": None, "value": 2}]}]
    assert "variants" not in pv and pv["size"] == 3
    assert dp.peer_view(groups, "b")["diffs"] == [], "no outlier"
    assert dp.peer_view(groups, "x") is None
    assert dp.outlier_map(pv) == {"p": {"majority": 2, "has_majority": True}}


def test_match_kind_and_variant():
    """match on kind/variant comes from the model table: one rule for all generations, no glob on names."""
    i4 = {"id": "shellyplusi4-b8d61a000101", "app": "PlusI4", "model": "SNSN-0024X", "gen": 2}
    mini = {"id": "shelly1minig3-84fce6000401", "app": "Mini1G3", "model": "S3SW-001X8EU", "gen": 3}
    pro = {"id": "shellypro2pm-0cb815000001", "app": "Pro2PM", "model": "SPSW-202PE16EU", "gen": 2}
    assert pol.profile_matches({"kind": "i4"}, i4) and pol.profile_matches({"kind": "I4"}, i4), "case does not matter"
    assert not pol.profile_matches({"kind": "i4"}, mini)
    assert pol.profile_matches({"kind": "1", "variant": "Mini"}, mini) and not pol.profile_matches({"kind": "1*"}, pro)
    assert pol.profile_matches({"kind": ["2PM", "i4"], "variant": "pro"}, pro), "list = or"
    assert pol.profile_matches({"kind": "unknown"}, {"id": "x", "app": "Brandnew", "model": "XX-1"})
    assert pol.validate({"profiles": [{"name": "A", "match": {"kind": "i4", "variant": "Standard"}, "settings": {}}]}) == []
    policy = {"profiles": [{"name": "Buttons", "match": {"app": "*I4*"}}, {"name": "ByKind", "match": {"kind": "i4"}},
                           {"name": "Mixed", "match": {"id": "shelly*"}}],
              "network": {"blocks": [{"name": "Mini", "match": {"id": "shelly1minig3-*"}, "range": "10.0.0.1-9"}]}}
    hints = pol.kind_hints(policy, [i4, mini, pro])
    assert [(h["what"], h["name"], h["kind"], h["devices"]) for h in hints] == [
        ("Profile", "Buttons", "i4", 1), ("Address block", "Mini", "1", 1)], "only globs whose devices share one kind"


def test_models():
    # Function first, then generation, form factor (Pro, Mini; Standard hidden) and suffix
    assert models.model_name("S4SW-002P16EU", "S2PMG4") == "Shelly 2PM · Gen4"
    assert models.model_name("S3SW-001P8EU") == "Shelly 1PM · Gen3 · Mini"
    assert models.model_name("SNSN-0024X", "PlusI4") == "Shelly i4 · Gen2", "Plus is Gen2, not part of the name"
    assert models.model_name("snsw-102p16eu") == "Shelly 2PM · Gen2", "case does not matter"
    assert models.model_name("SPSW-202PE16EU", None, 2) == "Shelly 2PM · Gen2 · Pro"
    assert models.model_name("SNSN-0D24X") == "Shelly i4 · Gen2 · DC"
    assert models.model_name("SNPL-00112UK") == "Shelly Plug · Gen2 · UK"
    t = models.device_type("SNSW-001X8EU", "Plus1Mini", 2)
    assert (t["kind"], t["variant"], t["suffix"], t["type_name"], t["type_sub"]) == ("1", "Mini", "", "Shelly 1", "Gen2 · Mini")
    assert models.device_type("SNSN-0D24X")["variant"] == "Standard", "DC is a suffix, not a form factor"
    assert models.device_type("S4SW-002P16EU", None, "5")["gen"] == 5, "the generation reported by the device wins"
    # unknown order number: from the firmware family
    assert models.model_name("XXXX-1", "Plus1PM") == "Shelly 1PM · Gen2"
    assert models.model_name("XXXX-1", "ProDimmer1PM") == "Shelly Pro Dimmer 1PM"
    assert models.model_name("XXXX-1", "Foo2PMG4") == "Shelly Foo 2PM · Gen4"
    assert models.device_type("XXXX-1", "Foo2PMG4")["kind"] == "unknown"
    assert models.model_name("XXXX-1", None) == "XXXX-1"
    assert models.model_name(None, None) == "Shelly"
    assert models.format_mac("1020BA000201") == "10:20:BA:00:02:01"
    assert models.format_mac("10-20-ba-00-02-01") == "10:20:BA:00:02:01"
    assert models.format_mac("10:20:ba:00:02:01") == "10:20:BA:00:02:01"
    assert models.format_mac("X") == "X", "anything unusable stays"
    assert models.format_mac(None) is None


def test_path_helpers():
    assert compare.path_matches("cover:*.motor", "cover:0.motor.idle_power_thr")
    assert compare.path_matches("*:*.name", "switch:0.name")
    assert not compare.path_matches("*:*.name", "sys.device.name")
    assert compare.version_key("2.1.1") > compare.version_key("2.0.3")
    assert compare.version_key("1.7.6-beta1") < compare.version_key("1.7.6")
    assert compare.version_key("1.10.0") > compare.version_key("1.9.9"), "numeric, not lexical"
    assert compare.version_key(None) < compare.version_key("0.0.1")



def test_stacks():
    """Three states per stack (off, on, on and connected) and a missing status."""
    from app.report import stacks
    assert stacks({"zigbee": {"enable": False}, "matter": {"enable": False}}, {}) == []
    assert stacks({}, {"zigbee": {"network_state": "joined"}}) == [], "no badge without config"
    on = stacks({"zigbee": {"enable": True}, "matter": {"enable": True}},
                {"zigbee": {"network_state": "steering"}, "matter": {"num_fabrics": 0}})
    assert [(s["stack"], s["connected"]) for s in on] == [("matter", None), ("zigbee", False)]
    assert stacks({"zigbee": {"enable": True}}, {})[0]["connected"] is None
    m = stacks({"matter": {"enable": True}}, {"matter": {"num_fabrics": 2}})[0]
    assert m["connected"] is None and m["detail"] == "Matter enabled (num_fabrics: 2)", "raw value only"
    assert on[0]["detail"] == "Matter enabled (num_fabrics: 0)"
    assert stacks({"matter": {"enable": True}}, {})[0]["detail"] == "Matter enabled"


def test_validate_profile_keys():
    """Unknown keys in profile/override and ignore under settings stand out."""
    errs = pol.validate({"profiles": [
        {"name": "test", "match": {"app": "S2PMG4ZB"}, "setting": {"ignore": ["wifi.ap.enable"]}},
        {"name": "Rollladen", "match": {"profile": "cover"},
         "settings": {"cover:*.obstruction_detection.enable": True, "ignore": ["wifi.ap.enable"]}}]})
    assert "profiles[0]: unknown field 'setting' (allowed: devices, exclude, ignore, match, name, settings)" in errs, errs
    assert "profiles[1].settings: 'ignore' belongs on profile level, not under settings" in errs, errs
    errs = pol.validate({"overrides": {"x": {"settings": {"ignore": []}, "ignor": ["a"]}}})
    assert "overrides.x: unknown field 'ignor' (allowed: ignore, settings)" in errs, errs
    assert "overrides.x.settings: 'ignore' belongs on override level, not under settings" in errs, errs
    assert pol.validate({"profiles": [{"name": "A", "devices": ["x"], "exclude": ["y"], "ignore": ["a"]}]}) == []
    pol.parse(open(pol.DEFAULT_POLICY_PATH, encoding="utf-8").read())   # the default policy stays valid


def test_ble_writable():
    """Bluetooth on/off is writable, nothing else under ble."""
    from app import writable
    assert writable.is_editable("ble.enable") and writable.is_editable("ble.rpc.enable")
    assert writable.is_editable("ble.observer.enable") and not writable.is_editable("ble.foo")
    assert writable.is_config_call("BLE.SetConfig", {"config": {"enable": False}})
    assert not writable.is_config_call("BLE.SetConfig", {"config": {"enable": False, "x": 1}})
    assert not writable.is_editable("wifi.sta.ssid"), "the Wi-Fi client stays blocked"
    # WebSocket, Matter, KNX
    assert writable.is_config_call("Ws.SetConfig", {"config": {"enable": True, "server": "ws://x:7011/shelly"}})
    assert writable.is_config_call("Matter.SetConfig", {"config": {"enable": False}})
    assert writable.is_config_call("KNX.SetConfig", {"config": {"ia": "1.1.5", "routing": {"addr": "224.0.23.12:3671"}}})
    assert not writable.is_editable("zigbee.enable"), "Zigbee stays blocked"
    assert writable.is_config_call("Modbus.SetConfig", {"config": {"enable": True}})
if __name__ == "__main__":
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
