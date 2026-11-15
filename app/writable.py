"""Allow list for editing the device configuration.

RelayRoster may write only these fields via `<Component>.SetConfig`. Keys relative to the component, as in the
schema in `app/devicepage.py`. Deliberately not included:
- `wifi.sta.*`, `wifi.sta1.*`: the device could lock itself out of the network
- passwords (`*.pass`, `Shelly.SetAuth`)
- `sys.device.profile`: changes the components of the device
- `ble.*` except Bluetooth on/off, RPC over Bluetooth and observer
- calibration and measured values (`MANAGED_PATHS`, `cover:*.motor.*`) and read-only values (`READ_ONLY_PATHS`)
- scripts, schedules, webhooks, KVS

The module has no dependencies, so `app/shelly.py` can use the check directly.
"""
from __future__ import annotations

import math
from typing import Any

# Component type -> RPC method. Indexed components (cover:0) need "id" in the call.
METHODS: dict[str, str] = {
    "sys": "Sys.SetConfig",
    "wifi": "WiFi.SetConfig",
    "cloud": "Cloud.SetConfig",
    "mqtt": "MQTT.SetConfig",
    "cover": "Cover.SetConfig",
    "switch": "Switch.SetConfig",
    "input": "Input.SetConfig",
    "ble": "BLE.SetConfig",
    "ws": "Ws.SetConfig",
    "matter": "Matter.SetConfig",
    "knx": "KNX.SetConfig",
    "modbus": "Modbus.SetConfig",
}
INDEXED = frozenset({"cover", "switch", "input"})

EDITABLE: dict[str, frozenset[str]] = {
    "sys": frozenset({
        "device.name", "device.eco_mode", "device.discoverable", "location.tz", "location.lat", "location.lon", "sntp.server",
        "debug.websocket.enable", "debug.mqtt.enable", "debug.udp.addr",
    }),
    "wifi": frozenset({"ap.enable"}),
    "cloud": frozenset({"enable"}),
    "mqtt": frozenset({
        "enable", "server", "client_id", "user", "topic_prefix", "ssl_ca", "rpc_ntf", "status_ntf",
        "use_client_cert", "enable_rpc", "enable_control",
    }),
    "cover": frozenset({
        "name", "in_mode", "in_locked", "maintenance_mode", "initial_state",
        "power_limit", "voltage_limit", "undervoltage_limit", "current_limit", "swap_inputs", "invert_directions",
        "obstruction_detection.enable", "obstruction_detection.direction", "obstruction_detection.action",
        "obstruction_detection.holdoff",
        "safety_switch.enable", "safety_switch.direction", "safety_switch.action",
        "slat.enable", "slat.open_time", "slat.close_time",
    }),
    "switch": frozenset({
        "name", "in_mode", "initial_state", "auto_on", "auto_on_delay", "auto_off", "auto_off_delay",
        "power_limit", "voltage_limit", "undervoltage_limit", "current_limit", "autorecover_voltage_errors",
    }),
    "input": frozenset({"name", "enable", "type", "invert", "factory_reset", "report_thr"}),
    # Bluetooth on/off. Without BLE, BTHome sensors and RPC over Bluetooth no longer work on this device
    "ble": frozenset({"enable", "rpc.enable", "observer.enable"}),
    # outbound WebSocket, Matter on/off, KNX (fields according to the Shelly docs Integrations/KNX)
    "ws": frozenset({"enable", "server", "ssl_ca"}),
    "matter": frozenset({"enable"}),
    "knx": frozenset({"enable", "ia", "routing.addr"}),
    "modbus": frozenset({"enable"}),   # Modbus TCP server on/off, the only field according to the Shelly docs
}

TEXT_MAX = 255


def split(path: str) -> tuple[str, int | None, str]:
    """'cover:0.power_limit' -> ('cover', 0, 'power_limit'); 'sys.location.tz' -> ('sys', None, 'location.tz')."""
    comp, _, key = path.partition(".")
    kind, sep, idx = comp.partition(":")
    if sep:
        if not idx.isdigit():
            raise ValueError(f"{path}: invalid component number")
        return kind, int(idx), key
    return kind, None, key


def is_editable(path: str) -> bool:
    try:
        kind, idx, key = split(path)
    except ValueError:
        return False
    if (idx is None) == (kind in INDEXED):
        return False
    return key in EDITABLE.get(kind, frozenset())


def _leaves(obj: Any, prefix: str = "") -> list[tuple[str, Any]] | None:
    """Leaves of a nested config object; None if anything other than objects and scalars occurs."""
    if not isinstance(obj, dict) or not obj:
        return None
    out: list[tuple[str, Any]] = []
    for k, v in obj.items():
        if not isinstance(k, str) or not k or "." in k:
            return None
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            sub = _leaves(v, path)
            if sub is None:
                return None
            out.extend(sub)
        elif v is None or isinstance(v, (bool, int, float, str)):
            out.append((path, v))
        else:
            return None
    return out


def is_config_call(method: str, params: Any) -> bool:
    """`<Component>.SetConfig` only with fields from EDITABLE, `id` exactly for indexed components."""
    kind = next((k for k, m in METHODS.items() if m == method), None)
    if kind is None or not isinstance(params, dict):
        return False
    want = {"id", "config"} if kind in INDEXED else {"config"}
    if set(params) != want:
        return False
    if kind in INDEXED and (not isinstance(params["id"], int) or isinstance(params["id"], bool) or params["id"] < 0):
        return False
    leaves = _leaves(params["config"])
    if not leaves:
        return False
    return all(key in EDITABLE[kind] for key, _ in leaves)


def validate(path: str, value: Any, meta: dict[str, Any], current: Any) -> Any:
    """Checks a new value against the schema (kind, options, min, max). Returns the value or ValueError."""
    kind = meta.get("kind")
    if path == "sys.device.name":   # as when renaming: text, 1 to 64 characters, no control characters
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must not be empty")
        value = value.strip()
        if len(value) > 64:
            raise ValueError("must be at most 64 characters")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("must not contain control characters")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError("must be on or off")
        return value
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("must be a number")
        lo = meta.get("min", 0)
        hi = meta.get("max")
        if lo is not None and value < lo:
            raise ValueError(f"must be at least {lo}")
        if hi is not None and value > hi:
            raise ValueError(f"must be at most {hi}")
        if isinstance(value, float) and value.is_integer() and isinstance(current, int):
            value = int(value)
        return value
    if kind == "enum":
        if value not in (meta.get("options") or []):
            opts = ", ".join("none" if o is None else str(o) for o in meta.get("options") or [])
            raise ValueError(f"must be one of {opts}")
        return value
    if kind == "text":
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("must be text")
        if len(value) > TEXT_MAX:
            raise ValueError(f"is longer than {TEXT_MAX} characters")
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("contains control characters")
        return value
    raise ValueError("cannot be changed here")


def build_calls(changes: dict[str, Any]) -> list[dict[str, Any]]:
    """Combines checked changes per component into one SetConfig call, in a stable order."""
    by_comp: dict[str, dict[str, Any]] = {}
    for path in sorted(changes):
        kind, idx, key = split(path)
        comp = path.split(".", 1)[0]
        entry = by_comp.setdefault(comp, {"component": comp, "method": METHODS[kind], "params": (
            {"id": idx, "config": {}} if idx is not None else {"config": {}}), "paths": []})
        node = entry["params"]["config"]
        parts = key.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = changes[path]
        entry["paths"].append(path)
    return list(by_comp.values())
