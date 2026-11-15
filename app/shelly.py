"""Client for the Shelly Gen2+ RPC API, read-only except for a few precisely checked write forms.

`call()` rejects every method that is not on the allow list (only `*.Get*` and `*.List*`).
Writing methods like `Sys.SetConfig`, `Shelly.Update` or `Shelly.Reboot` never reach the device
that way.

Exceptions with a precisely checked form, everything else fails with WriteBlocked:
- `set_name()`: `Sys.SetConfig` with exactly `{"config": {"device": {"name": <str>}}}` (`is_rename_call`)
- `check_for_update()` / `update_firmware()`: `Shelly.CheckForUpdate` without parameters and
  `Shelly.Update` with exactly `{"stage": "stable"|"beta"}` (`is_firmware_call`). A URL as firmware source
  is not allowed.
- `reboot()`: `Shelly.Reboot` without parameters (`is_reboot_call`), so also without `delay_ms`.
- `set_config()`: `<Component>.SetConfig` only with fields from the allow list in `app/writable.py`
  (`is_config_call`). No Wi-Fi client, no passwords, no calibration values. The device name is also
  written this way (Settings tab), the naming plan still uses `set_name()`.
- `set_auth()`: `Shelly.SetAuth` with exactly `{"user": "admin", "realm": <id>, "ha1": <sha256 hex>}`
  (`is_auth_call`). Disabling the password (`ha1: null`) is not allowed.
Whether renaming, updating, restarting or configuring is allowed is decided by the switches in Settings one
level up; this client only knows the allowed forms.
"""
from __future__ import annotations

import hashlib
import json
import ipaddress
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .writable import is_config_call

log = logging.getLogger(__name__)

_READ_ONLY = re.compile(r"^[A-Za-z0-9]+\.(Get[A-Za-z]*|List[A-Za-z]*)$")
# Get methods that trigger something despite their name stay blocked (none known at the moment).
_BLOCKED_ANYWAY: frozenset[str] = frozenset()


class WriteBlocked(PermissionError):
    """A call could have changed the device state."""


class RpcError(RuntimeError):
    def __init__(self, method: str, code: int | None, message: str):
        super().__init__(f"{method}: {message} (code {code})")
        self.method = method
        self.code = code


# Error codes that mean "this method/component does not exist on this device"
_NOT_SUPPORTED = {-114, 404, -109}


def is_read_only(method: str) -> bool:
    return bool(_READ_ONLY.match(method)) and method not in _BLOCKED_ANYWAY


NAME_MAX = 64
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_name(name: Any) -> str:
    """Checks a device name: text, no control characters, 1 to NAME_MAX characters after trimming."""
    if not isinstance(name, str):
        raise ValueError("Name must be text")
    name = name.strip()
    if not name:
        raise ValueError("Name must not be empty")
    if len(name) > NAME_MAX:
        raise ValueError(f"Name is longer than {NAME_MAX} characters")
    if _CONTROL.search(name):
        raise ValueError("Name contains control characters")
    return name


def is_rename_call(method: str, params: Any) -> bool:
    """Exactly Sys.SetConfig {"config": {"device": {"name": <valid name>}}}, no other key."""
    if method != "Sys.SetConfig" or not isinstance(params, dict) or set(params) != {"config"}:
        return False
    config = params["config"]
    if not isinstance(config, dict) or set(config) != {"device"}:
        return False
    device = config["device"]
    if not isinstance(device, dict) or set(device) != {"name"}:
        return False
    try:
        return clean_name(device["name"]) == device["name"]
    except ValueError:
        return False


FIRMWARE_STAGES = ("stable", "beta")


def is_firmware_call(method: str, params: Any) -> bool:
    """Shelly.CheckForUpdate without parameters or Shelly.Update with exactly {"stage": "stable"|"beta"}."""
    if method == "Shelly.CheckForUpdate":
        return params in (None, {})
    if method == "Shelly.Update":
        return (isinstance(params, dict) and set(params) == {"stage"}
                and params["stage"] in FIRMWARE_STAGES)
    return False


_HA1 = re.compile(r"[0-9a-f]{64}")


def is_auth_call(method: str, params: Any) -> bool:
    """Shelly.SetAuth with exactly user=admin, realm (device ID) and ha1 as SHA-256 hex. Never ha1=null."""
    if method != "Shelly.SetAuth" or not isinstance(params, dict) or set(params) != {"user", "realm", "ha1"}:
        return False
    realm, ha1 = params["realm"], params["ha1"]
    return (params["user"] == "admin" and isinstance(realm, str) and bool(realm)
            and isinstance(ha1, str) and bool(_HA1.fullmatch(ha1)))


def ha1(realm: str, password: str) -> str:
    return hashlib.sha256(f"admin:{realm}:{password}".encode()).hexdigest()


def is_reboot_call(method: str, params: Any) -> bool:
    """Shelly.Reboot without parameters. With delay_ms or anything else: blocked."""
    return method == "Shelly.Reboot" and params in (None, {})


_ADDRESS_KEYS = {"ipv4mode", "ip", "netmask", "gw", "nameserver"}


def _is_ipv4(value: Any) -> bool:
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return isinstance(value, str)


def is_address_call(method: str, params: Any) -> bool:
    """Static address only: WiFi.SetConfig {"config": {"sta": S}} or Eth.SetConfig {"config": S}, where S is
    exactly {"ipv4mode": "static", "ip", "netmask", "gw", "nameserver"} with IPv4 addresses (nameserver may be
    null). No SSID, no password, no other key."""
    if not isinstance(params, dict) or set(params) != {"config"} or not isinstance(params["config"], dict):
        return False
    cfg = params["config"]
    if method == "WiFi.SetConfig":
        if set(cfg) != {"sta"}:
            return False
        cfg = cfg["sta"]
    elif method != "Eth.SetConfig":
        return False
    if not isinstance(cfg, dict) or set(cfg) != _ADDRESS_KEYS or cfg["ipv4mode"] != "static":
        return False
    if not all(_is_ipv4(cfg[k]) for k in ("ip", "netmask", "gw")):
        return False
    try:
        ipaddress.IPv4Network(f"{cfg['gw']}/{cfg['netmask']}", strict=False)
    except ValueError:
        return False
    return cfg["nameserver"] is None or _is_ipv4(cfg["nameserver"])


@dataclass
class Device:
    host: str
    info: dict[str, Any]

    @property
    def id(self) -> str:
        return self.info.get("id") or self.host


class ShellyClient:
    """A client for one device. Shares the httpx client with all others."""

    def __init__(self, http: httpx.AsyncClient, host: str, password: str = "", timeout: float = 6.0):
        self.http = http
        self.host = host
        self.timeout = timeout
        self.auth = httpx.DigestAuth("admin", password) if password else None
        self._id = 0

    @property
    def base(self) -> str:
        return f"http://{self.host}"

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if not is_read_only(method):
            raise WriteBlocked(f"{method} is blocked (only read methods are allowed)")
        return await self._send(method, params)

    async def set_name(self, name: str) -> dict[str, Any]:
        """Writes sys.device.name. The only write action of this client."""
        params = {"config": {"device": {"name": clean_name(name)}}}
        if not is_rename_call("Sys.SetConfig", params):
            raise WriteBlocked("Sys.SetConfig is only allowed for the device name")
        return await self._send("Sys.SetConfig", params) or {}

    async def check_for_update(self) -> dict[str, Any]:
        """Lets the device ask the vendor for new firmware. Changes nothing on the device."""
        return await self._send("Shelly.CheckForUpdate", None) or {}

    async def update_firmware(self, stage: str) -> Any:
        """Starts the firmware update from channel `stage`. The device restarts afterwards."""
        params = {"stage": stage}
        if not is_firmware_call("Shelly.Update", params):
            raise WriteBlocked("Shelly.Update is only allowed with stage stable or beta")
        return await self._send("Shelly.Update", params)

    async def reboot(self) -> Any:
        """Restarts the device. The connection may drop before an answer arrives."""
        return await self._send("Shelly.Reboot", None)

    async def set_auth(self, realm: str, password: str) -> Any:
        """Sets the device password (user admin). Only in the form of is_auth_call."""
        if not password:
            raise ValueError("No device password configured")
        params = {"user": "admin", "realm": realm, "ha1": ha1(realm, password)}
        if not is_auth_call("Shelly.SetAuth", params):
            raise WriteBlocked("Shelly.SetAuth is only allowed to set a password")
        return await self._send("Shelly.SetAuth", params)

    async def set_static_ip(self, iface: str, ip: str, netmask: str, gw: str, nameserver: str | None) -> Any:
        """Gives the device a static address on its Wi-Fi client (`wifi`) or Ethernet (`eth`) interface.
        The device changes its address right away, so the answer may never arrive."""
        sta = {"ipv4mode": "static", "ip": ip, "netmask": netmask, "gw": gw, "nameserver": nameserver}
        method, params = (("Eth.SetConfig", {"config": sta}) if iface == "eth"
                          else ("WiFi.SetConfig", {"config": {"sta": sta}}))
        if not is_address_call(method, params):
            raise WriteBlocked(f"{method} is only allowed to set a static address")
        return await self._send(method, params)

    async def set_config(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Writes settings of a component. Only fields from app/writable.py, otherwise WriteBlocked."""
        if not is_config_call(method, params):
            raise WriteBlocked(f"{method} is only allowed for the editable settings")
        return await self._send(method, params) or {}

    async def _send(self, method: str, params: dict[str, Any] | None) -> Any:
        # Only reachable via call() (allow list) or the methods with a checked form
        if not (is_read_only(method) or is_rename_call(method, params) or is_firmware_call(method, params)
                or is_reboot_call(method, params) or is_config_call(method, params)
                or is_auth_call(method, params) or is_address_call(method, params)):
            raise WriteBlocked(f"{method} is blocked")
        self._id += 1
        body: dict[str, Any] = {"id": self._id, "method": method}
        if params:
            body["params"] = params
        resp = await self.http.post(f"{self.base}/rpc", json=body, auth=self.auth, timeout=self.timeout)
        if resp.status_code == 401:
            raise RpcError(method, 401, "Device requires a password (set SHELLY_PASSWORD)")
        if resp.status_code == 404:
            raise RpcError(method, 404, "Method not available")
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            err = data["error"] or {}
            raise RpcError(method, err.get("code"), err.get("message", "unknown error"))
        return data.get("result")

    async def call_optional(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Like call(), but returns None if the device does not know the method."""
        try:
            return await self.call(method, params)
        except RpcError as exc:
            if exc.code in _NOT_SUPPORTED:
                return None
            raise

    # ---- Combined reads ----------------------------------------------------------------------

    async def script_code(self, script_id: int) -> str:
        chunks: list[str] = []
        offset = 0
        for _ in range(256):  # protection against endless loops on broken answers
            res = await self.call("Script.GetCode", {"id": script_id, "offset": offset})
            data = res.get("data", "")
            chunks.append(data)
            offset += len(data.encode("utf-8"))
            if not res.get("left") or not data:
                break
        return "".join(chunks)

    async def kvs_all(self) -> dict[str, Any]:
        items: dict[str, Any] = {}
        offset = 0
        for _ in range(64):
            res = await self.call_optional("KVS.GetMany", {"match": "*", "offset": offset})
            if res is None:
                return items
            raw = res.get("items", [])
            # Older firmware: {"key": {"etag", "value"}}; newer: [{"key", "etag", "value"}]
            if isinstance(raw, dict):
                for key, val in raw.items():
                    items[key] = val.get("value") if isinstance(val, dict) else val
                count = len(raw)
            else:
                for entry in raw:
                    items[entry.get("key")] = entry.get("value")
                count = len(raw)
            total = res.get("total")
            offset += count
            if not count or total is None or offset >= total:
                break
        return items

    async def snapshot(self) -> dict[str, Any]:
        """Everything needed for backup and comparison, in one go."""
        info = await self.call("Shelly.GetDeviceInfo", {"ident": True})
        config = await self.call("Shelly.GetConfig")
        status = await self.call("Shelly.GetStatus")

        scripts: list[dict[str, Any]] = []
        script_list = await self.call_optional("Script.List")
        for s in (script_list or {}).get("scripts", []):
            entry = dict(s)
            entry["code"] = await self.script_code(s["id"])
            scripts.append(entry)

        schedules = (await self.call_optional("Schedule.List") or {}).get("jobs", [])
        webhooks = (await self.call_optional("Webhook.List") or {}).get("hooks", [])
        kvs = await self.kvs_all()

        return {
            "info": info,
            "config": config,
            "status": status,
            "scripts": scripts,
            "schedules": schedules,
            "webhooks": webhooks,
            "kvs": kvs,
        }


def backup_payload(snap: dict[str, Any]) -> dict[str, Any]:
    """The part of a snapshot that describes the target state (without runtime status)."""
    return {k: snap.get(k) for k in ("info", "config", "scripts", "schedules", "webhooks", "kvs")}


# Keys that change without anyone setting anything
_VOLATILE_INFO = {"ver", "fw_id", "auth_domain"}


def content_hash(snap: dict[str, Any]) -> str:
    """Hash over the target state. The firmware version does not count as a configuration change,
    neither does cfg_rev (it increases on every save, even without a change in content)."""
    payload = backup_payload(snap)
    info = {k: v for k, v in (payload.get("info") or {}).items() if k not in _VOLATILE_INFO}
    config = json.loads(json.dumps(payload.get("config") or {}))
    sys_cfg = config.get("sys")
    if isinstance(sys_cfg, dict):
        sys_cfg.pop("cfg_rev", None)
    payload = {**payload, "info": info, "config": config}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
