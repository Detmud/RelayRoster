"""Notifications via ntfy or webhook against the simulated fleet (issue #11).

Usage: python tests/test_notify.py   (or pytest)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

os.environ["RELAYROSTER_NO_AUTOAPP"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.collector import Collector, RenameError  # noqa: E402
from app.main import create_app  # noqa: E402
from app.notify import render  # noqa: E402
from app.store import Store  # noqa: E402
from fake_shelly import FakeShelly, base_config, make_fleet  # noqa: E402
from test_core import make_settings  # noqa: E402

H = {"X-Requested-With": "relayroster"}
NTFY = "https://ntfy.example.org/shelly-fleet"
HOOK = "http://homeassistant.local:8123/api/webhook/relayroster-abc"

PLUS1 = "shellyplus1-441793000301"
I4C = "shellyplusi4-b8d61a000103"


class Inbox:
    """Receives the outgoing notifications instead of ntfy and the webhook."""

    def __init__(self, status: int = 200):
        self.requests: list[httpx.Request] = []
        self.status = status

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status, json={"id": "x"})
        return httpx.MockTransport(handler)

    def ntfy(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "ntfy.example.org"]

    def hooks(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.host == "homeassistant.local"]

    def events(self, kind: str | None = None) -> list[dict]:
        return [e for h in self.hooks() for e in h["events"] if kind is None or e["type"] == kind]

    def clear(self) -> None:
        self.requests.clear()


def collector(fleet, tmp, inbox: Inbox, **kw) -> Collector:
    kw.setdefault("notify_ntfy_url", NTFY)
    kw.setdefault("notify_webhook_url", HOOK)
    s = make_settings(tmp, **kw)
    return Collector(Store(s.db_path), s, transport=fleet.transport(), notify_transport=inbox.transport())


def sync(col: Collector, scan: bool = False) -> None:
    async def go():
        await col.run(scan=scan)
        await col.notifier.flush()
    asyncio.run(go())


def test_offline_after_n_syncs_and_back_online():
    with tempfile.TemporaryDirectory() as tmp:
        fleet, inbox = make_fleet(), Inbox()
        col = collector(fleet, tmp, inbox, notify_ntfy_token="tk_secret")
        sync(col, scan=True)
        assert inbox.events("offline") == [] and inbox.events("new_device") == [], "first inventory is quiet"
        sync(col)
        assert inbox.requests == [], "a device that answers with an auth error (Fountain) is not offline"
        inbox.clear()

        dev = fleet.devices["192.168.10.41"]
        fleet.move(dev, None)                  # Plus1 is gone
        sync(col)
        assert inbox.events("offline") == [], "one failed sync is not offline yet (flapping)"
        sync(col)
        off = inbox.events("offline")
        assert [e["device_id"] for e in off] == [PLUS1]
        assert off[0]["failed_syncs"] == 2 and off[0]["host"] == "192.168.10.41"

        # ntfy: text body, title/tags/priority as query parameters, token as Bearer
        req = inbox.ntfy()[-1]
        assert str(req.url).startswith(NTFY + "?")
        assert req.url.params["priority"] == "4" and "red_circle" in req.url.params["tags"]
        assert req.url.params["title"] == "RelayRoster: Offline"
        assert req.headers["authorization"] == "Bearer tk_secret"
        assert "Bedroom Wall Switch" in req.content.decode() and "2 syncs" in req.content.decode()
        # webhook: JSON with the events
        hook = inbox.hooks()[-1]
        assert hook["source"] == "relayroster" and hook["test"] is False and hook["version"]

        inbox.clear()
        sync(col)
        assert inbox.requests == [], "offline is reported once, not after every sync"

        # Restart (new collector, same database): no repetition
        col = collector(fleet, tmp, inbox, notify_ntfy_token="tk_secret")
        sync(col)
        assert inbox.requests == []

        fleet.move(dev, "192.168.10.41")       # back
        sync(col)
        assert [e["device_id"] for e in inbox.events("online")] == [PLUS1]
        assert inbox.ntfy()[-1].url.params["priority"] == "3"
        inbox.clear()
        sync(col)
        assert inbox.requests == []

        # A single failed sync between good ones never sends anything
        fleet.move(dev, None)
        sync(col)
        fleet.move(dev, "192.168.10.41")
        sync(col)
        assert inbox.requests == [], "no online without offline before"


def test_new_device_from_scan():
    with tempfile.TemporaryDirectory() as tmp:
        fleet, inbox = make_fleet(), Inbox()
        col = collector(fleet, tmp, inbox)
        sync(col, scan=True)
        inbox.clear()
        new = FakeShelly("shellyplus1-441793000999", "Plus1", "SNSW-001X16EU", "1.7.5",
                         base_config("Garage Light", **{"switch:0": {"id": 0, "name": None, "in_mode": "follow",
                                                                     "initial_state": "off"}}))
        fleet.devices["192.168.10.77"] = new
        new.host, new.fleet = "192.168.10.77", fleet
        sync(col, scan=True)
        found = inbox.events("new_device")
        assert [(e["device_id"], e["host"], e["model"]) for e in found] == \
            [("shellyplus1-441793000999", "192.168.10.77", "SNSW-001X16EU")]
        assert "Garage Light" in inbox.ntfy()[-1].content.decode()
        inbox.clear()
        sync(col, scan=True)
        assert inbox.events("new_device") == [], "known now"


def test_new_drift_only():
    with tempfile.TemporaryDirectory() as tmp:
        fleet, inbox = make_fleet(), Inbox()
        col = collector(fleet, tmp, inbox)
        sync(col, scan=True)
        assert inbox.events("drift") == [], "existing findings set the baseline, no flood"
        sync(col)
        assert inbox.requests == []

        fleet.devices["192.168.10.23"].config["sys"]["location"]["tz"] = "UTC"
        sync(col)
        drift = inbox.events("drift")
        assert [(e["device_id"], e["path"]) for e in drift] == [(I4C, "sys.location.tz")]
        assert drift[0]["actual"] == "UTC" and drift[0]["kind"] == "mismatch"
        assert "Kitchen Wall Switch" in inbox.ntfy()[-1].content.decode()
        inbox.clear()
        sync(col)
        assert inbox.requests == [], "the same finding is not repeated"

        # Switched off: nothing, and switching on again starts with a fresh baseline
        col.settings.notify_drift = False
        fleet.devices["192.168.10.22"].config["sys"]["location"]["tz"] = "UTC"
        sync(col)
        assert inbox.requests == []
        col.settings.notify_drift = True
        sync(col)
        assert inbox.requests == [], "baseline after switching on"


def test_write_failures_are_batched():
    with tempfile.TemporaryDirectory() as tmp:
        fleet, inbox = make_fleet(), Inbox()
        col = collector(fleet, tmp, inbox)
        sync(col, scan=True)
        inbox.clear()
        for host in ("192.168.10.41", "192.168.10.23"):
            fleet.move(fleet.devices[host], None)

        async def go():
            col.notifier.batch_s = 0.05
            for dev_id in (PLUS1, I4C):
                try:
                    await col.rename(dev_id, "New Name")
                except RenameError:
                    pass
            assert inbox.requests == [], "collected first"
            await asyncio.sleep(0.2)
            await col.notifier.drain()
        asyncio.run(go())
        hooks = inbox.hooks()
        assert len(hooks) == 1, "both failures in one message"
        failed = hooks[0]["events"]
        assert [(e["type"], e["device_id"], e["action"]) for e in failed] == \
            [("write_failed", PLUS1, "rename"), ("write_failed", I4C, "rename")]
        assert failed[0]["detail"]
        assert inbox.ntfy()[0].url.params["priority"] == "4"

        # Failed entries without a device (settings, policy) and successful ones never count
        inbox.clear()
        col.store.add_audit("", "settings", None, None, False, "x")
        col.store.add_audit(PLUS1, "rename", "a", "b", True)
        asyncio.run(col.notifier.flush())
        assert inbox.requests == []


def test_switched_off_and_without_target():
    with tempfile.TemporaryDirectory() as tmp:
        fleet, inbox = make_fleet(), Inbox()
        col = collector(fleet, tmp, inbox, notify_offline=False, notify_write_failed=False,
                        notify_offline_after=1)
        sync(col, scan=True)
        fleet.move(fleet.devices["192.168.10.41"], None)
        sync(col)
        try:
            asyncio.run(col.rename(PLUS1, "X"))
        except RenameError:
            pass
        asyncio.run(col.notifier.flush())
        assert inbox.requests == []

        # Without any URL nothing is sent, but the state is still tracked
        col.settings.notify_offline = True
        col.settings.notify_ntfy_url = col.settings.notify_webhook_url = ""
        sync(col)
        assert inbox.requests == []
        assert PLUS1 in col.store.get_meta("notify:state")["offline"]


def test_failed_delivery_does_not_break_sync():
    with tempfile.TemporaryDirectory() as tmp:
        fleet, inbox = make_fleet(), Inbox(status=500)
        col = collector(fleet, tmp, inbox, notify_offline_after=1)
        sync(col, scan=True)
        fleet.move(fleet.devices["192.168.10.41"], None)
        sync(col)
        assert col.state.last_error is None
        last = col.store.get_meta("notify:last")
        assert last["results"]["ntfy"] == {"ok": False, "error": "HTTP 500"}

        def boom(request):
            raise httpx.ConnectError("no route", request=request)
        col.notifier.transport = httpx.MockTransport(boom)
        fleet.move(fleet.devices["192.168.10.23"], None)
        sync(col)
        assert col.state.last_error is None
        assert "ConnectError" in col.store.get_meta("notify:last")["results"]["webhook"]["error"]


def test_render_groups_and_limits():
    events = [{"type": "online", "device_id": "a", "device_name": "Ä-Light", "host": "1"},
              {"type": "offline", "device_id": "b", "device_name": None, "host": "2", "failed_syncs": 3,
               "error": "ConnectError"}]
    title, text = render(events)
    assert title == "RelayRoster: 1 × offline, 1 × back online"
    assert text.index("Offline:") < text.index("Back online:") and "Ä-Light (a)" in text
    many = [{"type": "drift", "device_id": f"d{i}", "path": "x", "source": "Baseline"} for i in range(100)]
    _, text = render(many)
    assert len(text.splitlines()) == 30 and text.endswith("more lines")


def test_settings_and_test_button():
    with tempfile.TemporaryDirectory() as tmp:
        fleet, inbox = make_fleet(), Inbox()
        app = create_app(make_settings(tmp), transport=fleet.transport(), autostart=False,
                         notify_transport=inbox.transport())
        c = TestClient(app)
        body = c.get("/api/settings").json()
        f = {x["key"]: x for x in body["fields"]}
        assert f["notify_ntfy_url"]["kind"] == "url" and f["notify_ntfy_url"]["group"] == "Notifications"
        assert f["notify_ntfy_token"]["kind"] == "password" and f["notify_offline_after"]["value"] == 2
        assert body["notify"] == {"targets": [], "last": None}

        r = c.post("/api/notify/test", headers=H)
        assert r.status_code == 400 and "first" in r.json()["detail"]
        assert c.post("/api/notify/test").status_code == 403, "CSRF"

        r = c.put("/api/settings", headers=H, json={"settings": {
            "notify_ntfy_url": "ftp://ntfy.example.org/x", "notify_webhook_url": "not a url",
            "notify_offline_after": 0}})
        assert r.status_code == 422
        assert set(r.json()["errors"]) == {"notify_ntfy_url", "notify_webhook_url", "notify_offline_after"}

        r = c.put("/api/settings", headers=H, json={"settings": {
            "notify_ntfy_url": " " + NTFY + " ", "notify_webhook_url": HOOK, "notify_ntfy_token": "tk_hidden"}})
        assert r.status_code == 200, r.text
        assert "tk_hidden" not in r.text and r.json()["notify"]["targets"] == ["ntfy", "webhook"]
        assert app.state.settings.notify_ntfy_url == NTFY
        audit = c.get("/api/audit?action=settings").json()["entries"][0]
        assert "tk_hidden" not in json.dumps(audit)

        r = c.post("/api/notify/test", headers=H)
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        assert r.json()["results"] == {"ntfy": {"ok": True, "error": None}, "webhook": {"ok": True, "error": None}}
        assert inbox.ntfy()[0].headers["authorization"] == "Bearer tk_hidden"
        assert inbox.hooks()[0]["test"] is True and inbox.hooks()[0]["events"] == []
        last = c.get("/api/settings").json()["notify"]["last"]
        assert last["test"] is True and last["results"]["ntfy"]["ok"] is True

        # The UI knows the URL field and the test button
        js = c.get("/static/js/settings.js").text
        assert 'f.kind === "url"' in js and "api/notify/test" in js and "data-clear" in js


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
