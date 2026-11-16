"""Apply the address plan: give a device a static IP address, one device after the other as a background job.

RelayRoster sets the address on the device itself (`WiFi.SetConfig` / `Eth.SetConfig` with `ipv4mode: static`),
not as a DHCP reservation: routers offer no common API for reservations.

Flow per device:
1. The target address must be free: no known device uses it and nothing answers on `/shelly` there
2. Check identity at the current address, read status and configuration, skip moving shutters
3. Netmask, gateway and DNS server come from the device's own static configuration; a device on DHCP has none,
   then `network.gateway`, `network.nameserver` and the matching `network.subnet` from the policy are used.
   Nothing is guessed: without these values the device is not moved
4. Set the address. The connection dropping right after is expected
5. Wait for the device at the new address and check its ID; only then store the new address and read it again
6. If it does not come back: report old and new address. There is no rollback on the device

The identity of a device stays its ID (MAC), never the address. Only one job runs at a time and never next to a
firmware or apply job.
"""
from __future__ import annotations

import asyncio
import ipaddress
import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from . import policy as pol
from .firmware import _starting_update
from .shelly import RpcError, ShellyClient, WriteBlocked
from .store import now_iso

if TYPE_CHECKING:
    from .collector import Collector

log = logging.getLogger(__name__)

_job_ids = itertools.count(1)
MOVING = ("opening", "closing", "calibrating")


class AddressError(RuntimeError):
    """Move not possible or failed. The message is meant for the UI."""


def _ip(value: Any) -> ipaddress.IPv4Address | None:
    try:
        return ipaddress.IPv4Address(str(value))
    except ValueError:
        return None


def plan_call(config: dict[str, Any] | None, status: dict[str, Any] | None, host: str,
              network: dict[str, Any] | None, target: str) -> dict[str, Any]:
    """Interface and values for the static address: {iface, method, netmask, gw, nameserver, source}.
    Raises AddressError when the device is not reached on its own address or a value is unknown."""
    config, status, network = config or {}, status or {}, network or {}
    wifi, eth = status.get("wifi") or {}, status.get("eth") or {}
    if eth.get("ip") == host:
        iface, method, cur = "eth", "Eth.SetConfig", config.get("eth") or {}
    elif wifi.get("sta_ip") == host:
        iface, method, cur = "wifi", "WiFi.SetConfig", (config.get("wifi") or {}).get("sta") or {}
        if wifi.get("ssid") and cur.get("ssid") and wifi["ssid"] != cur["ssid"]:
            raise AddressError(f"Connected to '{wifi['ssid']}', not to its main Wi-Fi '{cur['ssid']}' (fallback network). "
                               "Not moved.")
    else:
        own = wifi.get("sta_ip") or eth.get("ip") or "no address"
        raise AddressError(f"The device reports {own} as its address, RelayRoster reaches it at {host} "
                           "(NAT, range extender or old data). Not moved.")
    tgt = _ip(target)
    if tgt is None:
        raise AddressError(f"'{target}' is not an IPv4 address")
    if cur.get("ipv4mode") == "static" and cur.get("netmask") and cur.get("gw"):
        netmask, gw, nameserver, source = cur["netmask"], cur["gw"], cur.get("nameserver"), "device"
    else:
        gw, nameserver, source = network.get("gateway"), network.get("nameserver"), "policy"
        subnet = next((n for n in pol._networks(network.get("subnet")) if tgt in n), None)
        netmask = str(subnet.netmask) if subnet else None
        missing = [name for name, v in (("network.subnet", netmask), ("network.gateway", gw),
                                        ("network.nameserver", nameserver)) if not v]
        if missing:
            raise AddressError(f"The device uses DHCP, so it has no netmask, gateway and DNS server of its own. "
                               f"Set {', '.join(missing)} in the policy.")
    try:
        net = ipaddress.IPv4Network(f"{gw}/{netmask}", strict=False)
    except ValueError:
        raise AddressError(f"Gateway {gw} with netmask {netmask} is not a valid network") from None
    if tgt not in net:
        raise AddressError(f"{target} is not in the network of the gateway ({net})")
    if str(tgt) == str(gw):
        raise AddressError(f"{target} is the gateway")
    return {"iface": iface, "method": method, "netmask": str(netmask), "gw": str(gw),
            "nameserver": str(nameserver) if nameserver else None, "source": source}


@dataclass
class Item:
    device_id: str
    name: str | None
    old: str
    new: str
    status: str = "queued"     # queued, checking, moving, waiting, done, failed, skipped
    error: str | None = None
    started: float | None = None
    duration_s: int | None = None

    def view(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("device_id", "name", "old", "new", "status", "error", "duration_s")}


@dataclass
class Job:
    id: int
    items: list[Item]
    started_at: str = field(default_factory=now_iso)
    finished_at: str | None = None
    cancelled: bool = False

    @property
    def running(self) -> bool:
        return self.finished_at is None

    def view(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for it in self.items:
            counts[it.status] = counts.get(it.status, 0) + 1
        active = ("queued", "checking", "moving", "waiting")
        current = next((it for it in self.items if it.status in active[1:]), None)
        return {
            "id": self.id, "running": self.running, "cancelled": self.cancelled,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "total": len(self.items), "counts": counts,
            "done": sum(1 for it in self.items if it.status not in active),
            "current": current.view() if current else None,
            "items": [it.view() for it in self.items],
        }


class AddressMover:
    def __init__(self, collector: Collector, poll_s: float = 3.0, timeout_s: float = 90.0, probe_s: float = 3.0):
        self.collector = collector
        self.poll_s = poll_s
        self.timeout_s = timeout_s
        self.probe_s = probe_s
        self.job: Job | None = None
        self._task: asyncio.Task | None = None

    def start(self, moves: list[dict[str, Any]]) -> Job:
        """moves: [{device_id, ip}]. Devices are moved one after the other, in this order."""
        col = self.collector
        if self.job and self.job.running:
            raise RuntimeError("Addresses are already being changed")
        for other, what in ((col.firmware.job, "A firmware update"), (col.apply.job, "An apply job")):
            if other and other.running:
                raise RuntimeError(f"{what} is running, wait until it has finished")
        if not moves:
            raise ValueError("No devices selected")
        known = {d["id"]: d for d in col.store.devices()}
        hosts = {str(d["host"]): d["id"] for d in known.values()}
        items: list[Item] = []
        for mv in moves:
            dev_id, target = str(mv.get("device_id") or ""), str(mv.get("ip") or "").strip()
            dev = known.get(dev_id)
            if not dev:
                raise KeyError(dev_id)
            if _ip(target) is None:
                raise ValueError(f"{dev.get('name') or dev_id}: '{target}' is not an IPv4 address")
            if _ip(dev["host"]) is None:
                raise ValueError(f"{dev.get('name') or dev_id}: is reached by host name, not by address")
            if target == dev["host"]:
                raise ValueError(f"{dev.get('name') or dev_id}: already has {target}")
            if target in hosts:
                raise ValueError(f"{dev.get('name') or dev_id}: {target} is the address of {hosts[target]}")
            if any(it.new == target or it.device_id == dev_id for it in items):
                raise ValueError(f"{dev.get('name') or dev_id}: device or address {target} is listed twice")
            items.append(Item(device_id=dev_id, name=dev.get("name"), old=dev["host"], new=target))
        self.job = Job(id=next(_job_ids), items=items)
        self._task = asyncio.create_task(self._run(self.job))
        return self.job

    def cancel(self) -> bool:
        if not (self.job and self.job.running):
            return False
        self.job.cancelled = True
        return True

    async def wait(self) -> None:
        if self._task:
            await self._task

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self, job: Job) -> None:
        try:
            for item in job.items:
                if job.cancelled:
                    item.status, item.error = "skipped", "Cancelled before start"
                    continue
                item.started = time.monotonic()
                try:
                    await self._move_one(item)
                except AddressError as exc:
                    if item.status != "skipped":
                        item.status = "failed"
                    item.error = str(exc)
                except Exception as exc:  # never leave the job with half-set state
                    log.exception("Address change %s: unexpected error", item.device_id)
                    item.status, item.error = "failed", f"{type(exc).__name__}: {exc}"
                item.duration_s = int(time.monotonic() - item.started)
        finally:
            job.finished_at = now_iso()
            log.info("Address job %s finished: %s", job.id, job.view()["counts"])

    async def _move_one(self, item: Item) -> None:
        col = self.collector
        store = col.store
        dev = store.device(item.device_id)
        if not dev:
            raise AddressError("Device is no longer known to RelayRoster")
        item.old = dev["host"]
        target = item.new

        def fail(msg: str) -> AddressError:
            store.add_audit(item.device_id, "address", item.old, target, False, msg)
            log.warning("Address change %s failed: %s", item.device_id, msg)
            return AddressError(msg)

        def text(exc: Exception) -> str:
            return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__

        item.status = "checking"
        taken = next((d["id"] for d in store.devices() if d["host"] == target and d["id"] != item.device_id), None)
        if taken:
            raise fail(f"{target} is the address of {taken}")
        policy_data = None
        try:
            policy_data = pol.load(col.settings.policy_path)
        except Exception as exc:   # an invalid policy only matters for a device on DHCP, plan_call says so
            log.info("Address change: policy not usable (%s)", exc)

        async with col._http() as http:
            pw, timeout = col.settings.shelly_password, col.settings.rpc_timeout_s
            # 1. Nothing may answer at the target address. A device that is switched off is not noticed
            try:
                res = await http.get(f"http://{target}/shelly", timeout=self.probe_s)
            except httpx.HTTPError:
                res = None
            if res is not None:
                raise fail(f"Something already answers at {target} (HTTP {res.status_code}). Address is in use.")

            # 2. Identity, status and configuration from the device itself
            client = ShellyClient(http, item.old, pw, timeout)
            try:
                info = await client.call("Shelly.GetDeviceInfo") or {}
                if info.get("id") != item.device_id:
                    raise fail(f"{item.old} answers as {info.get('id')}, not {item.device_id}. Run a network scan first.")
                status = await client.call("Shelly.GetStatus") or {}
                config = await client.call("Shelly.GetConfig") or {}
            except AddressError:
                raise
            except (httpx.HTTPError, RpcError, WriteBlocked, ValueError) as exc:
                raise fail(text(exc)) from exc
            moving = sorted(k for k, v in status.items()
                            if k.startswith("cover:") and isinstance(v, dict) and v.get("state") in MOVING)
            if moving:
                item.status = "skipped"
                raise AddressError(f"{', '.join(moving)} is moving, try again when it has stopped")

            # 3. Values for the call
            try:
                call = plan_call(config, status, item.old, (policy_data or {}).get("network"), target)
            except AddressError as exc:
                raise fail(str(exc)) from None
            if not col.supports(item.device_id, call["method"]):
                raise fail(f"This device does not offer {call['method']}.")

            # 4. Set the address; the connection may drop before the answer arrives
            item.status = "moving"
            log.info("Address change %s: %s -> %s (%s)", item.device_id, item.old, target, call["iface"])
            answer: Any = None
            try:
                answer = await client.set_static_ip(call["iface"], target, call["netmask"], call["gw"], call["nameserver"])
            except Exception as exc:
                if not _starting_update(exc):
                    raise fail(text(exc)) from exc
            if isinstance(answer, dict) and answer.get("restart_required"):
                if not col.settings.allow_reboot:
                    raise fail(f"The address is stored, but the device only uses it after a restart, and restarting is "
                               f"turned off. Restart the device; it then answers at {target}. Run a network scan after that.")
                try:
                    await client.reboot()
                except Exception as exc:
                    if not _starting_update(exc):
                        raise fail(f"Address stored, restart failed: {text(exc)}") from exc

            # 5. Wait for the device at the new address; the ID decides, not the address
            item.status = "waiting"
            moved = ShellyClient(http, target, pw, timeout)
            deadline = time.monotonic() + self.timeout_s
            new_info: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                await asyncio.sleep(self.poll_s)
                try:
                    got = await moved.call("Shelly.GetDeviceInfo") or {}
                except (httpx.HTTPError, RpcError, ValueError):
                    continue
                if got.get("id") != item.device_id:
                    raise fail(f"{target} answers as {got.get('id')}, not {item.device_id}. Address conflict, "
                               f"check both devices.")
                new_info = got
                break
            if new_info is None:
                raise fail(f"The device did not answer at {target} within {int(self.timeout_s)} s (before: {item.old}). "
                           "Nothing is rolled back; check the device at both addresses.")
            col._seen(target, new_info)
            await col._poll_one(http, {**dev, "host": target})

        item.status = "done"
        store.add_audit(item.device_id, "address", item.old, target, True, f"static, {call['iface']}")
        log.info("Address change %s done: %s -> %s", item.device_id, item.old, target)
