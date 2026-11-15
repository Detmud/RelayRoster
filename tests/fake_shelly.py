"""Simulated Shelly fleet as an httpx transport. Logs every RPC method called,
so the tests can prove that RelayRoster only reads and only writes in the allowed forms
(name, firmware, restart, *.SetConfig from the allow list)."""
from __future__ import annotations

import copy
import json
from typing import Any

import httpx


_SET_CONFIG = {"Sys.SetConfig": "sys", "WiFi.SetConfig": "wifi", "Cloud.SetConfig": "cloud",
               "MQTT.SetConfig": "mqtt", "Cover.SetConfig": "cover", "Switch.SetConfig": "switch",
               "Input.SetConfig": "input"}


class _RpcFault(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class FakeShelly:
    def __init__(self, dev_id: str, app: str, model: str, ver: str, config: dict[str, Any], *,
                 gen: int | None = None, profile: str | None = None, stable: str | None = None,
                 scripts: list[dict[str, Any]] | None = None, kvs: dict[str, Any] | None = None,
                 kvs_dict_format: bool = False, password: str | None = None,
                 schedules: list[dict[str, Any]] | None = None, webhooks: list[dict[str, Any]] | None = None):
        self.id = dev_id
        self.app = app
        self.model = model
        self.ver = ver
        # like real devices: Gen3/Gen4 firmware families end in G3/G4, Plus and Pro are Gen2
        self.gen = gen or (int(app[-1]) if app[-2:] in ("G3", "G4") else 2)
        self.profile = profile
        self.config = config
        self.stable = stable
        self.scripts = scripts or []
        self.kvs = kvs or {}
        self.kvs_dict_format = kvs_dict_format
        self.password = password
        self.schedules = schedules or []
        self.webhooks = webhooks or []
        self.calls: list[str] = []
        self.writes: list[dict[str, Any]] = []   # parameters of every Sys.SetConfig
        self.ignore_name_writes = False          # simulates a device that does not take the name
        self.beta: str | None = None             # offered beta version
        self.updates: list[str] = []             # channel of every Shelly.Update
        self.update_fails = False                # device restarts but stays on the old version
        self.drop_on_update = False              # connection drops on the Shelly.Update call
        self.reboot_left = 0                     # this many requests still fail (restart)
        self.uptime = 12345                      # seconds since the last start
        self.reboots: list[Any] = []             # parameters of every Shelly.Reboot
        self.methods: list[str] | None = None    # Shelly.ListMethods; None = device does not know the method
        self.reboot_ignored = False              # simulates a device that does not perform the restart
        self.config_writes: list[tuple[str, dict[str, Any]]] = []   # (method, parameters) of every *.SetConfig
        self.config_error: str | None = None     # *.SetConfig fails with this message (e.g. shutter moving)
        self.config_ignore: set[str] = set()     # the device does not take these paths (read-back fails)
        self.cover_status: dict[str, dict[str, Any]] = {}   # additions to the cover status per component
        self._pending_ver: str | None = None

    @property
    def mac(self) -> str:
        return self.id.split("-")[-1].upper()

    def device_info(self) -> dict[str, Any]:
        info = {
            "name": self.config.get("sys", {}).get("device", {}).get("name"),
            "id": self.id, "mac": self.mac, "slot": 0, "model": self.model, "gen": self.gen,
            "fw_id": f"20261026-000000/{self.ver}-gabcdef", "ver": self.ver, "app": self.app,
            "auth_en": self.password is not None, "auth_domain": self.id if self.password else None,
        }
        if self.profile:
            info["profile"] = self.profile
        return info

    def offers(self) -> dict[str, Any]:
        upd: dict[str, Any] = {}
        if self.stable and self.stable != self.ver:
            upd["stable"] = {"version": self.stable}
        if self.beta and self.beta != self.ver:
            upd["beta"] = {"version": self.beta}
        return upd

    def status(self) -> dict[str, Any]:
        upd = self.offers()
        self.uptime += 1      # keeps running like on a real device
        out: dict[str, Any] = {
            "sys": {"uptime": self.uptime, "restart_required": False, "available_updates": upd},
            "wifi": {"sta_ip": "x", "status": "got ip", "ssid": "IoT", "rssi": -61},
        }
        for comp, cfg in self.config.items():
            if comp.startswith("cover:"):   # stehender, kalibrierter Rollladen
                out[comp] = {"id": cfg.get("id", 0), "source": "init", "state": "stopped", "apower": 0.0,
                             "voltage": 231.4, "current": 0.0, "pos_control": True, "current_pos": 100,
                             "last_direction": "open", "temperature": {"tC": 38.2, "tF": 100.8},
                             **self.cover_status.get(comp, {})}
        # Matter/Zigbee: enabled also means connected in the fake fleet
        if "zigbee" in self.config:
            out["zigbee"] = {"network_state": "joined" if self.config["zigbee"].get("enable") else "not_configured"}
        if "matter" in self.config:
            on = bool(self.config["matter"].get("enable"))
            out["matter"] = {"num_fabrics": 1 if on else 0, "commissionable": not on}
        return out

    def rpc(self, method: str, params: dict[str, Any]) -> Any:
        self.calls.append(method)
        if method == "Shelly.GetDeviceInfo":
            return self.device_info()
        if method == "Shelly.GetConfig":
            return copy.deepcopy(self.config)
        if method == "Shelly.GetStatus":
            return self.status()
        if method == "Script.List":
            return {"scripts": [{k: v for k, v in s.items() if k != "code"} for s in self.scripts]}
        if method == "Script.GetCode":
            code = next(s["code"] for s in self.scripts if s["id"] == params["id"]).encode()
            off = params.get("offset", 0)
            chunk = code[off:off + 20]  # small, so the paging logic is tested
            return {"data": chunk.decode(), "left": len(code) - off - len(chunk)}
        if method == "Schedule.List":
            return {"jobs": self.schedules, "rev": 1}
        if method == "Webhook.List":
            return {"hooks": self.webhooks, "rev": 1}
        if method == "Sys.GetStatus":
            return self.status()["sys"]
        if method == "Shelly.CheckForUpdate":
            return self.offers()
        if method == "Shelly.ListMethods":
            if self.methods is None:
                raise LookupError(method)
            return {"methods": list(self.methods)}
        if method == "Shelly.SetAuth":          # remember ha1, require digest auth from now on
            self.auth_ha1 = params.get("ha1")
            self.password = "(set)"
            return None
        if method == "Shelly.Reboot":
            self.reboots.append(copy.deepcopy(params))
            if not self.reboot_ignored:
                self.uptime = 0
                self.reboot_left = 2
            return None
        if method == "Shelly.Update":
            stage = params["stage"]
            self.updates.append(stage)
            target = (self.offers().get(stage) or {}).get("version")
            self._pending_ver = None if self.update_fails else target
            self.reboot_left = 3
            if self.drop_on_update:
                raise ConnectionResetError("closed")
            return None
        if method == "Sys.SetConfig" and set(params.get("config") or {}) == {"device"} \
                and set(params["config"]["device"]) == {"name"}:
            self.writes.append(copy.deepcopy(params))
            name = params["config"]["device"]["name"]
            if not self.ignore_name_writes:
                self.config.setdefault("sys", {}).setdefault("device", {})["name"] = name
                self.config["sys"]["cfg_rev"] = self.config["sys"].get("cfg_rev", 0) + 1
            return {"restart_required": False}
        if method in _SET_CONFIG:
            return self.set_config(method, params)
        if method == "KVS.GetMany":
            keys = sorted(self.kvs)
            off = params.get("offset", 0)
            page = keys[off:off + 2]
            if self.kvs_dict_format:
                items: Any = {k: {"etag": "e", "value": self.kvs[k]} for k in page}
            else:
                items = [{"key": k, "etag": "e", "value": self.kvs[k]} for k in page]
            return {"items": items, "offset": off, "total": len(keys)}
        raise LookupError(method)

    def set_config(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Like the firmware: take values into the component, increase cfg_rev."""
        self.config_writes.append((method, copy.deepcopy(params)))
        if self.config_error:
            raise _RpcFault(-103, self.config_error)
        kind = _SET_CONFIG[method]
        comp = f"{kind}:{params['id']}" if "id" in params else kind
        if comp not in self.config:
            raise _RpcFault(-105, f"Argument 'id', value {params.get('id')} not found!")

        def merge(dst: dict[str, Any], src: dict[str, Any], prefix: str) -> None:
            for k, v in src.items():
                if isinstance(v, dict):
                    merge(dst.setdefault(k, {}), v, f"{prefix}.{k}")
                elif f"{prefix}.{k}" not in self.config_ignore:
                    dst[k] = v
        merge(self.config[comp], params["config"], comp)
        self.config.setdefault("sys", {})["cfg_rev"] = self.config["sys"].get("cfg_rev", 0) + 1
        # Like the real firmware: MQTT and swapping the directions need a restart
        restart = kind == "mqtt" or "invert_directions" in params["config"]
        return {"restart_required": restart}

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/shelly":
            if self.gen == 1:
                return httpx.Response(200, json={"type": "SHSW-1", "mac": self.mac, "auth": False, "fw": "x"})
            return httpx.Response(200, json=self.device_info())
        if request.method == "POST" and request.url.path == "/rpc":
            if self.password is not None and "authorization" not in request.headers:
                return httpx.Response(401, headers={
                    "WWW-Authenticate": f'Digest qop="auth", realm="{self.id}", nonce="abc", algorithm=SHA-256'
                })
            body = json.loads(request.content)
            try:
                result = self.rpc(body["method"], body.get("params") or {})
            except LookupError:
                return httpx.Response(200, json={"id": body["id"], "src": self.id,
                                                 "error": {"code": -114, "message": "Method not found"}})
            except _RpcFault as fault:
                return httpx.Response(200, json={"id": body["id"], "src": self.id,
                                                 "error": {"code": fault.code, "message": fault.message}})
            return httpx.Response(200, json={"id": body["id"], "src": self.id, "result": result})
        return httpx.Response(404)


class Fleet:
    def __init__(self, devices: dict[str, FakeShelly]):
        self.devices = devices  # host -> FakeShelly

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            dev = self.devices.get(request.url.host)
            if dev is None:
                raise httpx.ConnectError("unreachable", request=request)
            if dev.reboot_left > 0:          # device is restarting right now
                dev.reboot_left -= 1
                if dev.reboot_left == 0 and dev._pending_ver:
                    dev.ver, dev._pending_ver = dev._pending_ver, None
                raise httpx.ConnectError("rebooting", request=request)
            try:
                return dev.handle(request)
            except ConnectionResetError as exc:
                raise httpx.RemoteProtocolError(str(exc), request=request) from None
        return httpx.MockTransport(handler)

    def all_calls(self) -> list[str]:
        return [c for d in self.devices.values() for c in d.calls]


def base_config(name: str, **extra: Any) -> dict[str, Any]:
    cfg = {
        "sys": {
            "device": {"name": name, "mac": "X", "eco_mode": False, "discoverable": True},
            "location": {"tz": "Europe/Berlin", "lat": 52.4, "lon": 13.5},
            "sntp": {"server": "time.google.com"},
            "cfg_rev": 7,
        },
        "wifi": {"ap": {"ssid": f"Shelly-{name}", "enable": False}, "sta": {"ssid": "IoT", "enable": True}},
        "cloud": {"enable": True, "server": "x"},
        "ble": {"enable": True},
        "mqtt": {"enable": False, "client_id": name},
    }
    cfg.update(extra)
    return cfg


def make_fleet(stacks: bool = False) -> Fleet:
    """`stacks=True` adds two 1PM Gen4 with Zigbee and Matter. Off by default, so the
    counts of the other tests stay the same; the demo turns it on."""
    i4a = FakeShelly("shellyplusi4-b8d61a000101", "PlusI4", "SNSN-0024X", "1.7.5",
                     base_config("Wall Switch A", **{"input:0": {"id": 0, "name": "Up", "type": "button", "enable": True, "invert": False, "factory_reset": True},
                                                     "input:1": {"id": 1, "name": None, "type": "button", "enable": True, "invert": False, "factory_reset": True}}),
                     stable="1.7.6")
    i4b = FakeShelly("shellyplusi4-b8d61a000102", "PlusI4", "SNSN-0024X", "1.7.6",
                     base_config("Wall Switch B", **{"input:0": {"id": 0, "name": "Light", "type": "button", "enable": True, "invert": False, "factory_reset": True},
                                                     "input:1": {"id": 1, "name": None, "type": "switch", "enable": True, "invert": False, "factory_reset": True}}))
    i4c = FakeShelly("shellyplusi4-b8d61a000103", "PlusI4", "SNSN-0024X", "1.7.6",
                     base_config("Kitchen Wall Switch", **{"input:0": {"id": 0, "name": None, "type": "button", "enable": True, "invert": False, "factory_reset": True},
                                                           "input:1": {"id": 1, "name": None, "type": "button", "enable": True, "invert": False, "factory_reset": True}}))
    # swapped: push-button inputs connected the other way round and corrected via swap_inputs (free via ignore)
    cover = lambda obstr, swapped=False: {  # noqa: E731
        "id": 0, "name": None, "in_mode": "dual", "initial_state": "stopped",
        "maxtime_open": 25.1, "maxtime_close": 24.7, "power_limit": 300, "in_locked": False, "maintenance_mode": False,
        "invert_directions": False, "swap_inputs": swapped,
        "obstruction_detection": {"enable": obstr, "direction": "both", "action": "stop", "power_thr": 160, "holdoff": 1}}
    g4a = FakeShelly("shelly2pmg4-1020ba000201", "S2PMG4", "S4SW-002P16EU", "2.1.1",
                     base_config("Home Office Shutter", **{"cover:0": cover(True)}), profile="cover",
                     scripts=[{"id": 1, "name": "frost_guard", "enable": True,
                               "code": "// frost guard\nlet t = 0;\nfunction tick() { t++; }\n"}],
                     kvs={"a": 1, "b": "two", "c": [3]})
    g4b = FakeShelly("shelly2pmg4-58e6c5000202", "S2PMG4", "S4SW-002P16EU", "2.1.1",
                     base_config("Bedroom Shutter", **{"cover:0": {**cover(False, swapped=True), "maxtime_open": 30.2}}),
                     profile="cover", kvs={"x": True}, kvs_dict_format=True)
    plus1 = FakeShelly("shellyplus1-441793000301", "Plus1", "SNSW-001X16EU", "1.7.5",
                       base_config("Bedroom Wall Switch", **{"switch:0": {"id": 0, "name": None, "in_mode": "detached",
                                                                          "initial_state": "restore_last"}}),
                       schedules=[{"id": 1, "enable": True, "timespec": "0 0 7 * * *", "calls": []}])
    plus1.config["sys"]["location"]["tz"] = "UTC"   # drift against the baseline
    plus1.config["wifi"]["ap"]["enable"] = True      # drift against the baseline
    locked = FakeShelly("shelly1minig3-84fce6000401", "Mini1G3", "S3SW-001X8EU", "2.0.1",
                        base_config("Fountain"), password="secret-pw")
    gen1 = FakeShelly("shelly1-aabbcc", "x", "SHSW-1", "1.14.0", {}, gen=1)
    extra: dict[str, FakeShelly] = {}
    if stacks:
        for host, mac, name, zb in (("192.168.10.33", "1020bafd4410", "Kitchen Light", True),
                                    ("192.168.10.34", "1020bafd4420", "Hallway Light", False)):
            extra[host] = FakeShelly(
                f"shelly1pmg4-{mac}", "S1PMG4", "S4SW-001P16EU", "2.1.1",
                base_config(name, **{"switch:0": {"id": 0, "name": None, "in_mode": "follow", "initial_state": "off"},
                                     "zigbee": {"enable": zb}, "matter": {"enable": not zb}}))
    return Fleet({
        **extra,
        "192.168.10.21": i4a, "192.168.10.22": i4b, "192.168.10.23": i4c,
        "192.168.10.31": g4a, "192.168.10.32": g4b, "192.168.10.41": plus1,
        "192.168.10.51": locked, "192.168.10.99": gen1,
    })
