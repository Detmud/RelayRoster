"""Combines store data and policy into the views of the UI."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import compare, policy as pol
from .models import format_mac, model_name
from .store import Store


def _latest_configs(store: Store, devices: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    configs: dict[str, dict[str, Any]] = {}
    for d in devices:
        if d.get("last_snapshot"):
            snap = store.snapshot(d["last_snapshot"])
            if snap:
                configs[d["id"]] = snap["data"].get("config") or {}
    return configs


# Matter/Zigbee. Keys according to the Shelly API (Gen4): config `matter.enable` / `zigbee.enable`,
# status `zigbee.network_state` (joined = in the network) and `matter.num_fabrics`. What Shelly counts in num_fabrics is
# not certain (Mini1G3 with 2, but not connected). Matter stays grey, tooltip only with the raw value.
STACKS = (("matter", "Matter"), ("zigbee", "Zigbee"))


def stacks(config: dict[str, Any], status: dict[str, Any]) -> list[dict[str, Any]]:
    """Only enabled stacks. `connected`: True/False, None if the status does not tell."""
    out = []
    for key, label in STACKS:
        if (config.get(key) or {}).get("enable") is not True:
            continue
        st = status.get(key) or {}
        if key == "zigbee":
            state = st.get("network_state")
            connected = state == "joined" if state else None
            detail = f"Zigbee network: {state}" if state else "Zigbee on, network state unknown"
        else:
            fabrics = st.get("num_fabrics")
            connected = None
            detail = f"Matter enabled (num_fabrics: {fabrics})" if fabrics is not None else "Matter enabled"
        out.append({"stack": key, "label": label, "connected": connected, "detail": detail})
    return out


def newest_versions(devices: list[dict[str, Any]]) -> dict[str, str]:
    newest: dict[str, str] = {}
    for d in devices:
        app, ver = d.get("app") or "", d.get("ver")
        if ver and (app not in newest or compare.version_key(ver) > compare.version_key(newest[app])):
            newest[app] = ver
    return newest


def _updates(status: dict[str, Any] | None) -> dict[str, str | None]:
    upd = ((status or {}).get("sys") or {}).get("available_updates") or {}
    return {
        "stable": (upd.get("stable") or {}).get("version"),
        "beta": (upd.get("beta") or {}).get("version"),
    }


def _stale(device: dict[str, Any], minutes: int) -> bool:
    if not device.get("last_ok"):
        return True
    try:
        last = datetime.fromisoformat(device["last_ok"])
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - last).total_seconds() > minutes * 60 * 2.5


# Capabilities from Shelly.ListMethods. None = unknown (device not read yet or without ListMethods)
CAP_METHODS = {"update": "Shelly.Update", "reboot": "Shelly.Reboot", "set_auth": "Shelly.SetAuth"}


def caps(store: Store, device_id: str) -> dict[str, bool] | None:
    methods = store.device_methods(device_id)
    return None if methods is None else {k: m.lower() in methods for k, m in CAP_METHODS.items()}


def build(store: Store, policy_data: dict[str, Any] | None, poll_minutes: int) -> dict[str, Any]:
    devices = store.devices()
    configs = _latest_configs(store, devices)
    newest = newest_versions(devices)
    used_ips = {d["host"] for d in devices}
    planned = (policy_data or {}).get("names") or {}
    rows: list[dict[str, Any]] = []
    all_findings: list[dict[str, Any]] = []
    for d in devices:
        status = d.get("status") or {}
        wifi = status.get("wifi") or {}
        sys = status.get("sys") or {}
        findings = (
            pol.evaluate(policy_data, d, configs.get(d["id"]), status, newest, used_ips, reserve_addresses=True)
            if policy_data else []
        )
        for f in findings:
            all_findings.append({**f, "device_id": d["id"], "device_name": d.get("name")})
        snaps = store.snapshots(d["id"])
        rows.append({
            "id": d["id"],
            "name": d.get("name"),
            "host": d["host"],
            "mac": format_mac(d.get("mac")),
            "app": d.get("app"),
            "model": d.get("model"),
            "model_name": model_name(d.get("model"), d.get("app")),
            "gen": d.get("gen"),
            "profile": d.get("profile"),
            "ver": d.get("ver"),
            "updates": _updates(status),
            "auth_en": d.get("auth_en"),
            "rssi": wifi.get("rssi"),
            "ssid": wifi.get("ssid"),
            "uptime": sys.get("uptime"),
            "restart_required": sys.get("restart_required"),
            "last_ok": d.get("last_ok"),
            "last_error": d.get("last_error"),
            "stale": _stale(d, poll_minutes),
            "profiles": pol.matching_profiles(policy_data, d) if policy_data else [],
            "stacks": stacks(configs.get(d["id"]) or {}, status),
            "caps": caps(store, d["id"]),
            "findings": len(findings),
            "address": next(({"tag": f["tag"], "note": f["note"]} for f in findings if f["kind"] == "network"), None),
            "planned_name": planned.get(d["id"]),
            "has_snapshot": d["id"] in configs,
            "snapshot_count": len(snaps),
            "last_change": snaps[0]["taken_at"] if snaps else None,
            "last_confirmed": snaps[0]["confirmed_at"] if snaps else None,
            "last_snapshot": snaps[0]["id"] if snaps else None,
        })
    all_findings, stale, not_applicable = classify_missing(devices, configs, all_findings)
    counts: dict[str, int] = {}
    for f in all_findings:
        counts[f["device_id"]] = counts.get(f["device_id"], 0) + 1
    for r in rows:
        r["findings"] = counts.get(r["id"], 0)
    # Entries in the naming plan without a known device: usually a typo in the ID
    names_unknown = sorted(set(planned) - {d["id"] for d in devices})
    return {"devices": rows, "findings": all_findings, "configs": configs, "newest": newest,
            "names_unknown": names_unknown, "stale_rules": stale, "not_applicable": not_applicable}


def _type_key(d: dict[str, Any]) -> str:
    return str(d.get("model") or d.get("app") or "?")


def classify_missing(devices: list[dict[str, Any]], configs: dict[str, dict[str, Any]],
                     findings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]],
                                                              list[dict[str, Any]]]:
    """Splits findings of kind 'missing'. Not every Shelly has every setting:

    - no device of the fleet has the path: outdated rule. No finding per device, but an entry in stale
      (e.g. ble.enable, which no longer exists since firmware 2.0)
    - no device of the same model has the path: does not apply to this model (not_applicable, does not count)
    - other devices of the same model have it: stays 'missing', with a hint to their firmware
    """
    by_id = {d["id"]: d for d in devices}
    flats = {dev_id: compare.flatten(cfg) for dev_id, cfg in configs.items()}
    has_cache: dict[tuple[str, str], bool] = {}

    def has(dev_id: str, pattern: str) -> bool:
        key = (dev_id, pattern)
        if key not in has_cache:
            has_cache[key] = bool(compare.expand_pattern(pattern, flats[dev_id]))
        return has_cache[key]

    keep: list[dict[str, Any]] = []
    stale: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    na: list[dict[str, Any]] = []
    for f in findings:
        if f.get("kind") != "missing":
            keep.append(f)
            continue
        pattern, dev = f["path"], by_id.get(f["device_id"]) or {}
        holders = [i for i in flats if has(i, pattern)]
        if not holders:
            key = (f["source"], pattern, f["device_id"] if f["source"] == "Override" else None)
            entry = stale.setdefault(key, {"source": f["source"], "path": pattern, "device_id": key[2],
                                           "expected": f.get("expected"), "devices": []})
            entry["devices"].append(f["device_id"])
            continue
        same = [by_id[i] for i in holders if i in by_id and _type_key(by_id[i]) == _type_key(dev)]
        if not same:
            na.append({"device_id": f["device_id"], "path": pattern, "source": f["source"],
                       "expected": f.get("expected"),
                       "note": "No device of this model has this setting"})
            continue
        vers = sorted({d.get("ver") for d in same if d.get("ver")}, key=compare.version_key)
        name = model_name(dev.get("model"), dev.get("app"))
        if vers and dev.get("ver") and compare.version_key(dev["ver"]) < compare.version_key(vers[-1]):
            note = (f"Present on {len(same)} other {name} with firmware {vers[0]}"
                    + (f" to {vers[-1]}" if len(vers) > 1 else "") + ". Update the firmware.")
        else:
            note = f"Present on {len(same)} other {name} with the same or older firmware"
        keep.append({**f, "note": note})
    return keep, sorted(stale.values(), key=lambda e: (e["source"], e["path"])), na


def firmware_view(devices: list[dict[str, Any]], newest: dict[str, str]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for d in devices:
        groups.setdefault(d.get("app") or "?", []).append(d)
    out = []
    for app, members in sorted(groups.items()):
        stable = sorted({m["updates"]["stable"] for m in members if m["updates"]["stable"]}, key=compare.version_key)
        out.append({
            "app": app,
            "model": members[0].get("model"),
            "model_name": model_name(members[0].get("model"), app),
            "newest_installed": newest.get(app),
            "stable_available": stable[-1] if stable else None,
            "uniform": len({m.get("ver") for m in members}) == 1,
            "devices": sorted(
                [{"id": m["id"], "name": m["name"], "mac": m.get("mac"), "ver": m["ver"], "updates": m["updates"]}
                 for m in members],
                key=lambda x: compare.version_key(x["ver"]),
            ),
        })
    return out
