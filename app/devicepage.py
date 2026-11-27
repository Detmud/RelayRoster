"""Device page: configuration readable per component, deviations from its own type.

The schema here describes known settings (type, unit, options, range). Editing uses it for
inputs and validation; which fields are writable is in app/writable.py. Unknown settings
derive type and display from the value, so new firmware fields do not disappear.
"""
from __future__ import annotations

from typing import Any

from . import compare, policy as pol, writable

# ---- Schema ---------------------------------------------------------------------------------
# kind: bool, number, enum, text. Without kind, the type of the value applies. Keys relative to the component.

_NAME = {"name": {"label": "Name", "kind": "text"}}
_STA = {
    "enable": {"label": "Enabled", "kind": "bool"},
    "ssid": {"label": "SSID", "kind": "text"},
    "ipv4mode": {"label": "IPv4 mode", "kind": "enum", "options": ["dhcp", "static"]},
    "ip": {"label": "Static IP", "kind": "text"},
    "netmask": {"label": "Netmask", "kind": "text"},
    "gw": {"label": "Gateway", "kind": "text"},
    "nameserver": {"label": "DNS server", "kind": "text"},
}

SCHEMA: dict[str, dict[str, Any]] = {
    "sys": {"title": "System", "fields": {
        "device.name": {"label": "Device name", "kind": "text"},
        "device.eco_mode": {"label": "Eco mode", "kind": "bool"},
        "device.discoverable": {"label": "Discoverable", "kind": "bool"},
        "location.tz": {"label": "Time zone", "kind": "text"},
        "location.lat": {"label": "Latitude", "kind": "number", "unit": "°", "min": -90, "max": 90},
        "location.lon": {"label": "Longitude", "kind": "number", "unit": "°", "min": -180, "max": 180},
        "sntp.server": {"label": "Time server (SNTP)", "kind": "text"},
        "debug.websocket.enable": {"label": "Debug log via WebSocket", "kind": "bool"},
        "debug.mqtt.enable": {"label": "Debug log via MQTT", "kind": "bool"},
        "debug.udp.addr": {"label": "Debug log UDP address", "kind": "text"},
        "rpc_udp.dst_addr": {"label": "RPC over UDP: destination", "kind": "text"},
        "rpc_udp.listen_port": {"label": "RPC over UDP: listen port", "kind": "number"},
    }},
    "wifi": {"title": "Wi-Fi", "fields": {
        **{f"sta.{k}": v for k, v in _STA.items()},
        **{f"sta1.{k}": {**v, "label": f"Fallback: {v['label']}"} for k, v in _STA.items()},
        "ap.enable": {"label": "Access point", "kind": "bool"},
        "ap.ssid": {"label": "Access point SSID", "kind": "text"},
        "ap.range_extender.enable": {"label": "Range extender", "kind": "bool"},
        "roam.rssi_thr": {"label": "Roaming threshold", "kind": "number", "unit": "dBm"},
        "roam.interval": {"label": "Roaming interval", "kind": "number", "unit": "s"},
    }},
    "eth": {"title": "Ethernet", "fields": {"enable": {"label": "Enabled", "kind": "bool"}, **{
        k: v for k, v in _STA.items() if k not in ("enable", "ssid")}}},
    "cloud": {"title": "Cloud", "fields": {
        "enable": {"label": "Enabled", "kind": "bool"},
        "server": {"label": "Server", "kind": "text"},
    }},
    "mqtt": {"title": "MQTT", "fields": {
        "enable": {"label": "Enabled", "kind": "bool"},
        "server": {"label": "Server", "kind": "text"},
        "client_id": {"label": "Client ID", "kind": "text"},
        "user": {"label": "User", "kind": "text"},
        "topic_prefix": {"label": "Topic prefix", "kind": "text"},
        "ssl_ca": {"label": "TLS", "kind": "enum", "options": [None, "*", "user_ca.pem", "ca.pem"]},
        "rpc_ntf": {"label": "RPC notifications", "kind": "bool"},
        "status_ntf": {"label": "Status notifications", "kind": "bool"},
        "use_client_cert": {"label": "Client certificate", "kind": "bool"},
        "enable_rpc": {"label": "RPC over MQTT", "kind": "bool"},
        "enable_control": {"label": "Control over MQTT", "kind": "bool"},
    }},
    "ws": {"title": "Outbound WebSocket", "fields": {
        "enable": {"label": "Enabled", "kind": "bool"},
        "server": {"label": "Server", "kind": "text"},
        "ssl_ca": {"label": "TLS", "kind": "enum", "options": [None, "*", "user_ca.pem", "ca.pem"]},
    }},
    "matter": {"title": "Matter", "fields": {
        "enable": {"label": "Enabled", "kind": "bool"},
    }},
    "modbus": {"title": "Modbus", "fields": {
        "enable": {"label": "Enabled", "kind": "bool"},
    }},
    "knx": {"title": "KNX", "fields": {
        "enable": {"label": "Enabled", "kind": "bool"},
        "ia": {"label": "Individual address", "kind": "text"},
        "routing.addr": {"label": "Routing multicast address", "kind": "text"},
    }},
    "ble": {"title": "Bluetooth", "fields": {
        "enable": {"label": "Enabled", "kind": "bool"},
        "rpc.enable": {"label": "RPC over Bluetooth", "kind": "bool"},
        "observer.enable": {"label": "Observer (BLU devices)", "kind": "bool"},
    }},
    "cover": {"title": "Cover", "fields": {
        **_NAME,
        "in_mode": {"label": "Input mode", "kind": "enum", "options": ["single", "dual", "detached"]},
        "in_locked": {"label": "Ignore local buttons", "kind": "bool"},
        "maintenance_mode": {"label": "Maintenance mode", "kind": "bool"},
        "initial_state": {"label": "State after power on", "kind": "enum", "options": ["open", "closed", "stopped"]},
        "power_limit": {"label": "Power limit", "kind": "number", "unit": "W"},
        "voltage_limit": {"label": "Overvoltage limit", "kind": "number", "unit": "V"},
        "undervoltage_limit": {"label": "Undervoltage limit", "kind": "number", "unit": "V"},
        "current_limit": {"label": "Current limit", "kind": "number", "unit": "A"},
        "maxtime_open": {"label": "Max. time to open", "kind": "number", "unit": "s"},
        "maxtime_close": {"label": "Max. time to close", "kind": "number", "unit": "s"},
        "swap_inputs": {"label": "Swap inputs", "kind": "bool"},
        "invert_directions": {"label": "Invert directions", "kind": "bool"},
        "motor.idle_power_thr": {"label": "Motor idle power", "kind": "number", "unit": "W"},
        "motor.idle_confirm_period": {"label": "Motor idle confirm time", "kind": "number", "unit": "s"},
        "obstruction_detection.enable": {"label": "Obstruction detection", "kind": "bool"},
        "obstruction_detection.direction": {"label": "Obstruction: direction", "kind": "enum",
                                            "options": ["open", "close", "both"]},
        "obstruction_detection.action": {"label": "Obstruction: action", "kind": "enum", "options": ["stop", "reverse"]},
        "obstruction_detection.power_thr": {"label": "Obstruction: power threshold", "kind": "number", "unit": "W"},
        "obstruction_detection.holdoff": {"label": "Obstruction: hold-off", "kind": "number", "unit": "s",
                                          "min": 0.1, "max": 300},
        "safety_switch.enable": {"label": "Safety switch", "kind": "bool"},
        "safety_switch.direction": {"label": "Safety switch: direction", "kind": "enum",
                                    "options": ["open", "close", "both"]},
        "safety_switch.action": {"label": "Safety switch: action", "kind": "enum", "options": ["stop", "reverse", "pause"]},
        "slat.enable": {"label": "Slat control", "kind": "bool"},
        "slat.open_time": {"label": "Slat: open time", "kind": "number", "unit": "s"},
        "slat.close_time": {"label": "Slat: close time", "kind": "number", "unit": "s"},
    }},
    "switch": {"title": "Switch", "fields": {
        **_NAME,
        "in_mode": {"label": "Input mode", "kind": "enum",
                    "options": ["momentary", "follow", "flip", "detached", "cycle", "activate"]},
        "initial_state": {"label": "State after power on", "kind": "enum",
                          "options": ["off", "on", "restore_last", "match_input"]},
        "auto_on": {"label": "Auto on", "kind": "bool"},
        "auto_on_delay": {"label": "Auto on delay", "kind": "number", "unit": "s"},
        "auto_off": {"label": "Auto off", "kind": "bool"},
        "auto_off_delay": {"label": "Auto off delay", "kind": "number", "unit": "s"},
        "power_limit": {"label": "Power limit", "kind": "number", "unit": "W"},
        "voltage_limit": {"label": "Overvoltage limit", "kind": "number", "unit": "V"},
        "undervoltage_limit": {"label": "Undervoltage limit", "kind": "number", "unit": "V"},
        "current_limit": {"label": "Current limit", "kind": "number", "unit": "A"},
        "autorecover_voltage_errors": {"label": "Recover after voltage error", "kind": "bool"},
        "power_restore_delay_enable": {"label": "Delay after power restore", "kind": "bool"},
        "power_restore_delay": {"label": "Power restore delay", "kind": "number"},
    }},
    "input": {"title": "Input", "fields": {
        **_NAME,
        "enable": {"label": "Enabled", "kind": "bool"},
        "type": {"label": "Type", "kind": "enum", "options": ["button", "switch", "analog", "count"]},
        "invert": {"label": "Invert", "kind": "bool"},
        "factory_reset": {"label": "Factory reset by toggling", "kind": "bool"},
        "report_thr": {"label": "Report threshold", "kind": "number", "unit": "%"},
    }},
    "light": {"title": "Light", "fields": {
        **_NAME,
        "initial_state": {"label": "State after power on", "kind": "enum", "options": ["off", "on", "restore_last"]},
        "auto_on": {"label": "Auto on", "kind": "bool"},
        "auto_on_delay": {"label": "Auto on delay", "kind": "number", "unit": "s"},
        "auto_off": {"label": "Auto off", "kind": "bool"},
        "auto_off_delay": {"label": "Auto off delay", "kind": "number", "unit": "s"},
        "default.brightness": {"label": "Default brightness", "kind": "number", "unit": "%", "min": 0, "max": 100},
    }},
}
_ORDER = ["sys", "wifi", "eth", "cloud", "mqtt", "ws", "ble"]
_TITLE_EXTRA = {"bthome": "BTHome", "ui": "Display", "knx": "KNX", "matter": "Matter", "zigbee": "Zigbee",
                "modbus": "Modbus", "plugs_ui": "LED", "wd": "Watchdog"}


def _comp_type(comp: str) -> str:
    return comp.split(":", 1)[0]


def component_title(comp: str, cfg: dict[str, Any] | None = None) -> str:
    kind, _, idx = comp.partition(":")
    base = (SCHEMA.get(kind) or {}).get("title") or _TITLE_EXTRA.get(kind) or kind.replace("_", " ").capitalize()
    title = f"{base} {idx}" if idx else base
    name = (cfg or {}).get("name") if isinstance(cfg, dict) else None
    return f"{title} · {name}" if isinstance(name, str) and name else title


def _humanize(key: str) -> str:
    text = " › ".join(part.replace("_", " ") for part in key.split("."))
    return text[:1].upper() + text[1:]


def _infer_kind(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, (list, dict)):
        return "object"
    return "text"


def field_meta(path: str, value: Any) -> dict[str, Any]:
    """Display of a path: label, type, unit, options from SCHEMA or from the value."""
    comp, _, key = path.partition(".")
    spec = ((SCHEMA.get(_comp_type(comp)) or {}).get("fields") or {}).get(key) or {}
    kind = spec.get("kind") or _infer_kind(value)
    if isinstance(value, (list, dict)):
        kind = "object"
    meta = {"label": spec.get("label") or _humanize(key or comp), "kind": kind, "known": bool(spec)}
    for k in ("unit", "options", "min", "max"):
        if k in spec:
            meta[k] = spec[k]
    return meta


# Paths of findings that are not device settings
_SYNTHETIC_LABELS = {
    "firmware.stable": "Stable firmware update",
    "firmware.peer": "Firmware behind its type",
    "auth": "Password protection",
    "ip": "IP address",
    "ip.block": "Address block",
    "sys.device.name": "Device name",
}


def setting_label(path: str) -> str:
    """Readable name of a path for findings and the peer comparison: "switch:0.auto_off" -> "Switch 0 · Auto off".
    The path itself stays visible in the UI, because the policy is written in paths."""
    if path in _SYNTHETIC_LABELS:
        return _SYNTHETIC_LABELS[path]
    comp, _, key = path.partition(".")
    title = component_title(comp)
    if not key:
        return title
    if _comp_type(comp) == "cover" and key == "pos_control":
        return f"{title} · Calibration"
    return f"{title} · {field_meta(path, None)['label']}"


def use_value(expected: Any) -> Any:
    """Value that "Use …" suggests for a policy rule: fixed target, otherwise the limit if only min or max.
    None if the rule names no single value (in, not, min and max together)."""
    if isinstance(expected, dict):
        if set(expected) == {"max"} or set(expected) == {"min"}:
            v = next(iter(expected.values()))
            return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        return None
    return None if isinstance(expected, list) else expected


def _sort_key(comp: str) -> tuple:
    kind, _, idx = comp.partition(":")
    if kind in _ORDER:
        return (0, _ORDER.index(kind), 0, "")
    return (1, kind, int(idx) if idx.isdigit() else 0, comp)


def settings_view(config: dict[str, Any] | None, *, concrete: dict[str, tuple[Any, str]] | None = None,
                  depends: list[dict[str, Any]] | None = None, outliers: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Configuration per component for the Settings tab.

    concrete: policy target per path (from policy.resolve); depends: peer_depends; outliers: {path: {majority,
    has_majority}} from the peer comparison, if this device is an outlier there.
    Read-only and managed values are listed separately under "info", settings without effect carry
    active=False and the reason.
    """
    flat = compare.flatten(config or {})
    concrete, depends, outliers = concrete or {}, depends or [], outliers or {}
    sections: dict[str, dict[str, Any]] = {}
    info: list[dict[str, Any]] = []
    for path in sorted(flat):
        value = flat[path]
        comp = path.split(".", 1)[0]
        meta = field_meta(path, value)
        field: dict[str, Any] = {"path": path, "value": value, **meta}
        field["editable"] = meta["kind"] in ("bool", "number", "enum", "text") and writable.is_editable(path)
        # The number of a component (cover:0.id) is fixed, not changeable
        read_only = compare.matches_any(compare.READ_ONLY_PATHS, path) or (":" in comp and path == f"{comp}.id")
        if read_only or compare.matches_any(compare.MANAGED_PATHS, path):
            field["reason"] = "read_only" if read_only else "managed"
            field["section"] = component_title(comp, (config or {}).get(comp))
            info.append(field)
            continue
        rules = compare.depends_for(path, depends)
        field["active"] = not rules or compare.active_on(rules, path, flat)
        if not field["active"]:
            field["needs"] = compare.describe_when(rules, path)
        if path in concrete:
            expected, source = concrete[path]
            field["policy"] = {"expected": pol.describe_expected(expected), "source": source,
                               "ok": pol.value_ok(expected, value)}
            target = use_value(expected)
            if target is not None:
                field["policy"]["value"] = target       # "Use …" takes it into the draft
        if path in outliers:
            field["peer"] = outliers[path]
        sec = sections.setdefault(comp, {"id": comp, "title": component_title(comp, (config or {}).get(comp)),
                                         "fields": []})
        sec["fields"].append(field)
        rel = path.split(".", 1)[1] if "." in path else ""
        if rel == "enable" and isinstance(value, bool):
            sec["enabled"] = value
    ordered = [sections[c] for c in sorted(sections, key=_sort_key)]
    for sec in ordered:
        # "enable" first, then known fields in schema order, unknown ones afterwards alphabetically
        order = list(((SCHEMA.get(_comp_type(sec["id"])) or {}).get("fields") or {}).keys())
        sec["fields"].sort(key=lambda f: (f["path"].split(".", 1)[1] != "enable",
                                          order.index(f["path"].split(".", 1)[1]) if f["known"] else len(order),
                                          f["path"]))
    return {"sections": ordered, "info": info}


def peer_view(groups: list[dict[str, Any]], device_id: str) -> dict[str, Any] | None:
    """Where does this device deviate from the others of its type (peer comparison)?"""
    for g in groups:
        if not any(d["id"] == device_id for d in g["devices"]):
            continue
        others = [d for d in g["devices"] if d["id"] != device_id]

        def row(r: dict[str, Any]) -> dict[str, Any]:
            vals = r["values"]
            return {"path": r["path"], "label": setting_label(r["path"]), "value": vals.get(device_id),
                    "others": [{"id": d["id"], "name": d.get("name"), "value": vals.get(d["id"])} for d in others]}

        diffs = [{**row(r), "majority": r["majority"], "has_majority": r["has_majority"]}
                 for r in g["diffs"] if device_id in r["outliers"]]
        return {"app": g["app"], "profile": g["profile"], "model_name": g["model_name"], "size": len(g["devices"]),
                "diffs": diffs, "group_diffs": len(g["diffs"])}
    return None


def outlier_map(peer: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {r["path"]: {"majority": r["majority"], "has_majority": r["has_majority"]}
            for r in (peer or {}).get("diffs") or []}


# Shutter card: measured values from the last status next to the limits from the configuration. This
# helps to set power_limit and power_thr per motor (override). The power only applies to the moment of reading:
# if the shutter is not moving then, it shows 0 W
COVER_MOVING = ("opening", "closing", "calibrating")


def moving_covers(status: Any) -> list[str]:
    """Shutters that are moving right now, from Shelly.GetStatus, e.g. ["cover:0"]."""
    return sorted(k for k, v in (status or {}).items()
                  if k.startswith("cover:") and isinstance(v, dict) and v.get("state") in COVER_MOVING)


def cover_view(config: dict[str, Any] | None, status: dict[str, Any] | None) -> list[dict[str, Any]]:
    config, status = config or {}, status or {}
    comps = sorted({c for c in (*config, *status) if c.startswith("cover:")}, key=_sort_key)
    out: list[dict[str, Any]] = []
    for comp in comps:
        cfg = config.get(comp) if isinstance(config.get(comp), dict) else {}
        st = status.get(comp) if isinstance(status.get(comp), dict) else {}
        obstr = cfg.get("obstruction_detection") if isinstance(cfg.get("obstruction_detection"), dict) else {}
        temp = st.get("temperature") if isinstance(st.get("temperature"), dict) else {}
        errors = st.get("errors") if isinstance(st.get("errors"), list) else []
        out.append({
            "id": comp,
            "title": component_title(comp, cfg),
            "state": st.get("state"),
            "moving": st.get("state") in COVER_MOVING,
            "calibrated": st.get("pos_control"),
            "position": st.get("current_pos"),
            "last_direction": st.get("last_direction"),
            "apower": st.get("apower"),
            "voltage": st.get("voltage"),
            "current": st.get("current"),
            "temperature": temp.get("tC"),
            "errors": [str(e) for e in errors],
            "limits": {
                "power_limit": cfg.get("power_limit"),
                "power_thr": obstr.get("power_thr"),
                "holdoff": obstr.get("holdoff"),
                "current_limit": cfg.get("current_limit"),
                "voltage_limit": cfg.get("voltage_limit"),
                "undervoltage_limit": cfg.get("undervoltage_limit"),
                "maxtime_open": cfg.get("maxtime_open"),
                "maxtime_close": cfg.get("maxtime_close"),
            },
            "obstruction": {k: obstr.get(k) for k in ("enable", "direction", "action")},
        })
    return out
