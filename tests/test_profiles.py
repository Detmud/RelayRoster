"""Profiles in the UI: round trip of policy.yaml with comments, API, backups.

Usage: python tests/test_profiles.py   (or pytest)
"""
from __future__ import annotations

import asyncio
import os
import sys
import re
import tempfile

os.environ["RELAYROSTER_NO_AUTOAPP"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient  # noqa: E402

from app import policy as pol  # noqa: E402
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


def test_policy_edit_keeps_comments():
    """Round trip with ruamel.yaml. Unchanged = identical byte for byte, comments move with their entry,
    text that PyYAML would read as bool gets quotes."""
    from app import policy_edit as pe
    text = open(pol.DEFAULT_POLICY_PATH, encoding="utf-8").read()
    doc = pe.load_doc(text)
    assert pe.dump_doc(doc) == text
    pe.reorder_profiles(doc, ["Button input", "Shutter"])
    out = pe.dump_doc(doc)
    assert out.index("# Shelly i4 as a pure button input") < out.index("- name: Button input") \
        < out.index("# Roller shutters") < out.index("- name: Shutter") < out.index("# Overrides for single")
    assert "# detection is off during hold-off, keep it short" in out
    pe.reorder_profiles(doc, ["Shutter", "Button input"])
    assert pe.dump_doc(doc) == text, "sorting there and back gives the original file"

    shutter = pe.list_profiles(doc)[0]
    shutter["settings"] = [r for r in shutter["settings"] if r["path"] != "cover:*.in_mode"]
    shutter["settings"].append({"path": "cover:*.initial_state", "op": "in", "value": ["on", "stopped"], "comment": "x"})
    try:
        pe.upsert_profile(doc, shutter, "Shutter")
    except pe.EditError as exc:
        assert "appears twice" in exc.errors[0]
    else:
        raise AssertionError("a duplicate path must be rejected")
    shutter["settings"].pop()
    shutter["settings"].append({"path": "switch:*.initial_state", "op": "in", "value": ["on", "off"], "comment": "y"})
    pe.upsert_profile(doc, shutter, "Shutter")
    out = pe.dump_doc(doc)
    assert "cover:*.in_mode: dual" not in out and "# two buttons, up and down" not in out
    assert 'switch:*.initial_state: {in: ["on", "off"]}' in out
    data = pol.parse(out)
    assert data["profiles"][0]["settings"]["switch:*.initial_state"] == {"in": ["on", "off"]}

    pe.delete_profile(doc, "Shutter")
    out = pe.dump_doc(doc)
    assert "# Roller shutters" not in out and "# Calibration is checked" not in out
    assert "# Shelly i4 as a pure button input" in out and "# Overrides for single devices" in out
    pe.set_override(doc, "shellyplusi4-b8d61a000102", "input:1.type", "switch", "latching rocker switch")
    out = pe.dump_doc(doc)
    assert "overrides:\n  shellyplusi4-b8d61a000102:\n    settings:\n      input:1.type: switch # latching rocker switch\n" in out
    assert pol.parse(out)["overrides"]["shellyplusi4-b8d61a000102"]["settings"] == {"input:1.type": "switch"}


def test_profiles_api_roundtrip():
    """Create, change, reorder and delete profiles without YAML. Comments stay, every change creates a
    backup, invalid input changes nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        _, app, c = setup(tmp)
        path = app.state.settings.policy_path
        orig = open(path, encoding="utf-8").read()
        body = c.get("/api/profiles").json()
        names = [p["name"] for p in body["profiles"]]
        assert names == ["Shutter", "Button input"]
        shutter = body["profiles"][0]
        assert len(shutter["devices"]) == 2 and shutter["findings"] >= 1
        rule = next(r for r in shutter["settings"] if r["path"] == "cover:*.obstruction_detection.holdoff")
        assert rule == {"path": "cover:*.obstruction_detection.holdoff", "op": "max", "value": 1,
                        "comment": "detection is off during hold-off, keep it short"}
        assert any(x["value"] == "S2PMG4" and x["label"] == "Shelly 2PM Gen4" for x in body["choices"]["app"])
        assert [t["name"] for t in body["templates"]] == ["Shutter", "Button input"]

        # Preview: nothing saved, matching devices, actual values and deviations
        plug = {"name": "Plus 1 relay", "match": {"app": "Plus1"},
                "settings": [{"path": "switch:*.initial_state", "op": "in", "value": ["on", "restore_last"],
                              "comment": "keep state after power failure"}], "ignore": []}
        assert c.post("/api/profiles/preview", json={"profile": plug}).status_code == 403, "CSRF"
        r = c.post("/api/profiles/preview", json={"profile": plug}, headers=H)
        assert r.status_code == 200, r.text
        pv = r.json()
        assert [d["app"] for d in pv["devices"]] == ["Plus1"]
        assert pv["rules"][0]["devices"][0]["values"][0]["path"] == "switch:0.initial_state"
        assert "switch:*.initial_state" in pv["paths"] and not any("power_thr" in p for p in pv["paths"])
        assert open(path, encoding="utf-8").read() == orig
        bad = c.post("/api/profiles/preview", json={"profile": {**plug, "match": {}}}, headers=H)
        assert bad.status_code == 422 and "Match needs at least one field, or assign devices" in bad.json()["errors"]

        # Create at position 1, "on" must stay a string for PyYAML
        r = c.post("/api/profiles", json={"profile": plug, "position": 1}, headers=H)
        assert r.status_code == 200, r.text
        text = open(path, encoding="utf-8").read()
        assert 'switch:*.initial_state: {in: ["on", restore_last]}' in text
        assert "# keep state after power failure" in text
        assert "# detection is off during hold-off, keep it short" in text
        assert [p["name"] for p in c.get("/api/profiles").json()["profiles"]] == ["Shutter", "Plus 1 relay",
                                                                                "Button input"]
        backups = [f for f in os.listdir(tmp) if f.startswith("policy.yaml.bak-")]
        assert len(backups) == 1
        assert open(os.path.join(tmp, backups[0]), encoding="utf-8").read() == orig
        assert c.post("/api/profiles", json={"profile": plug}, headers=H).status_code == 409, "Name doppelt"

        # Change and rename
        plug2 = {**plug, "name": "Relay", "settings": [{**plug["settings"][0], "op": "=", "value": "off"}]}
        r = c.put("/api/profiles/Plus 1 relay", json={"profile": plug2}, headers=H)
        assert r.status_code == 200, r.text
        prof = next(p for p in c.get("/api/profiles").json()["profiles"] if p["name"] == "Relay")
        assert prof["settings"][0]["value"] == "off" and prof["devices"]
        assert c.put("/api/profiles/gibtsnicht", json={"profile": plug2}, headers=H).status_code == 404
        r = c.put("/api/profiles/Relay", json={"profile": {**plug2, "settings": [{"path": "x", "op": "max",
                                                                                     "value": "a"}]}}, headers=H)
        assert r.status_code == 422 and "x: 'max' needs a number" in r.json()["errors"]

        # Reorder and delete
        r = c.post("/api/profiles/order", json={"names": ["Button input", "Relay", "Shutter"]}, headers=H)
        assert r.status_code == 200, r.text
        assert [p["name"] for p in c.get("/api/profiles").json()["profiles"]] == ["Button input", "Relay", "Shutter"]
        assert c.post("/api/profiles/order", json={"names": ["Relay"]}, headers=H).status_code == 422
        assert c.delete("/api/profiles/Relay", headers=H).status_code == 200
        text = open(path, encoding="utf-8").read()
        assert "Relay" not in text.replace("RelayRoster", "") and "# keep state after power failure" not in text
        assert "# Roller shutters" in text and "# Shelly i4 as a pure button input" in text
        assert c.get("/api/overview").json()["policy_errors"] == []

        # Override from the device page, comment required
        dev = "shelly2pmg4-1020ba000201"
        rule = {"path": "cover:0.power_limit", "op": "=", "value": 180}
        r = c.post(f"/api/devices/{dev}/override", json={"rule": rule}, headers=H)
        assert r.status_code == 422 and "comment is required" in r.json()["errors"][0]
        r = c.post(f"/api/devices/{dev}/override", json={"rule": {**rule, "comment": "measured 140 W"}}, headers=H)
        assert r.status_code == 200, r.text
        det = c.get(f"/api/devices/{dev}").json()
        assert any(x["path"] == "cover:0.power_limit" and x["source"] == "Override" for x in det["rules"])
        assert "cover:0.power_limit: 180" in open(path, encoding="utf-8").read()

        # Invalid file: profiles cannot be changed until it is repaired in the YAML editor
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("baseline: [broken\n")
        assert c.get("/api/profiles").json()["errors"]
        assert c.delete("/api/profiles/Shutter", headers=H).status_code == 409


def test_policy_backups_are_limited():
    with tempfile.TemporaryDirectory() as tmp:
        _, app, c = setup(tmp)
        text = c.get("/api/policy").json()["text"]
        for i in range(25):
            assert c.put("/api/policy", json={"text": text + f"# {i}\n"}, headers=H).status_code == 200
        backups = [f for f in os.listdir(tmp) if f.startswith("policy.yaml.bak-")]
        assert len(backups) == 20


def test_profiles_page_assets():
    """Profiles page in sidebar, page header and app.js."""
    with tempfile.TemporaryDirectory() as tmp:
        _, _, c = setup(tmp)
        html = c.get("/").text
        assert 'href="#profiles" data-page="profiles"' in html and 'id="tab-profiles"' in html
        assert 'data-actions="profiles" href="#profiles/new"' in html
        js = all_js(c)
        assert "profiles: {" in js and "api/profiles/preview" in js and "/override`" in js


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
