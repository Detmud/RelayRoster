"""Apply the address plan: give a device a static IP address, one device after the other as a background job.

RelayRoster sets the address on the device itself (`WiFi.SetConfig` / `Eth.SetConfig` with `ipv4mode: static`),
not as a DHCP reservation: routers offer no common API for reservations.

Flow per device:
1. The target address must be free: no known device uses it and nothing answers on `/shelly` there. The
   device's current address is allowed too: that switches it from DHCP to a static address in place
2. Check identity at the current address, read status and configuration, skip moving shutters
3. Netmask, gateway and DNS server come from `network` in the policy only (see `plan_call`). Nothing is taken
   from the device or guessed: unless subnet, DHCP pool, gateway, DNS server and the target fit together, the
   device is not moved
4. Set the address. The connection dropping right after is expected
5. Wait for the device at the new address and check its ID and that the static values are stored; only then
   store the new address and read it again
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
              network: dict[str, Any] | None, target: str, block: dict[str, Any] | None = None) -> dict[str, Any]:
    """Interface and values for the static address: {iface, method, netmask, gw, nameserver, source}.

    Netmask, gateway and DNS server come from `network` in the policy only, never from the device or a guess.
    Raises AddressError unless every value is known and fits: the target is a host address of network.subnet,
    outside network.dhcp_pool, inside the device's block (if it has one), and neither gateway nor DNS server;
    the gateway is in the same subnet. Also when the device is not reached on its own address."""
    config, status, network = config or {}, status or {}, network or {}
    wifi, eth = status.get("wifi") or {}, status.get("eth") or {}
    if eth.get("ip") == host:
        iface, method = "eth", "Eth.SetConfig"
    elif wifi.get("sta_ip") == host:
        iface, method = "wifi", "WiFi.SetConfig"
        cur = (config.get("wifi") or {}).get("sta") or {}
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
    missing = [f"network.{k}" for k in ("subnet", "dhcp_pool", "gateway", "nameserver") if not network.get(k)]
    if missing:
        raise AddressError(f"Set {', '.join(missing)} in the policy. RelayRoster writes a static address only with "
                           "netmask, gateway, DNS server and DHCP pool from there.")
    try:
        subnets, pools = pol._networks(network["subnet"]), pol._networks(network["dhcp_pool"])
    except ValueError as exc:
        raise AddressError(f"Policy: {exc}") from None
    gw, nameserver = _ip(network["gateway"]), _ip(network["nameserver"])
    if gw is None or nameserver is None:
        raise AddressError("Policy: network.gateway and network.nameserver must be IPv4 addresses")
    subnet = next((n for n in subnets if tgt in n), None)
    if subnet is None:
        raise AddressError(f"{target} is not in network.subnet ({', '.join(str(n) for n in subnets)})")
    if gw not in subnet:
        raise AddressError(f"Gateway {gw} is not in {subnet}, the subnet of {target}")
    if tgt in (subnet.network_address, subnet.broadcast_address):
        raise AddressError(f"{target} is the {'network' if tgt == subnet.network_address else 'broadcast'} address "
                           f"of {subnet}")
    if tgt == gw:
        raise AddressError(f"{target} is the gateway")
    if tgt == nameserver:
        raise AddressError(f"{target} is the DNS server")
    pool = next((p for p in pools if tgt in p), None)
    if pool is not None:
        raise AddressError(f"{target} is in the DHCP pool {pool}; the router could hand it to another device")
    if block:
        first, last = pol.parse_range(block["range"])
        if not first <= tgt <= last:
            raise AddressError(f"{target} is not in block '{block.get('name') or block['range']}' ({block['range']})")
    return {"iface": iface, "method": method, "netmask": str(subnet.netmask), "gw": str(gw),
            "nameserver": str(nameserver), "source": "policy"}


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
            if hosts.get(target, dev_id) != dev_id:
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
        except Exception as exc:   # plan_call then names the missing network values
            log.info("Address change: policy not usable (%s)", exc)

        async with col._http() as http:
            pw, timeout = col.settings.shelly_password, col.settings.rpc_timeout_s
            # 1. Nothing may answer at the target address. A device that is switched off is not noticed.
            # Keeping the current address (DHCP -> static) skips this: the device itself answers there
            res = None
            if target != item.old:
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
                call = plan_call(config, status, item.old, (policy_data or {}).get("network"), target,
                                 pol.address_block(policy_data or {}, dev))
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
            wrong: list[str] = []
            while time.monotonic() < deadline:
                await asyncio.sleep(self.poll_s)
                try:
                    got = await moved.call("Shelly.GetDeviceInfo") or {}
                except (httpx.HTTPError, RpcError, ValueError):
                    continue
                if got.get("id") != item.device_id:
                    raise fail(f"{target} answers as {got.get('id')}, not {item.device_id}. Address conflict, "
                               f"check both devices.")
                # An answer alone proves nothing when the address stays the same (DHCP -> static in place)
                try:
                    cfg = await moved.call("Shelly.GetConfig") or {}
                except (httpx.HTTPError, RpcError, ValueError):
                    continue
                cfg = (cfg.get("eth") if call["iface"] == "eth" else (cfg.get("wifi") or {}).get("sta")) or {}
                wanted = {"ipv4mode": "static", "ip": target, "netmask": call["netmask"], "gw": call["gw"],
                          "nameserver": call["nameserver"]}
                wrong = [k for k, v in wanted.items() if cfg.get(k) != v]
                if wrong:
                    continue
                new_info = got
                break
            if new_info is None and wrong:
                raise fail(f"The device answers at {target}, but did not store the static address "
                           f"({', '.join(wrong)} differ). Check its web UI.")
            if new_info is None:
                raise fail(f"The device did not answer at {target} within {int(self.timeout_s)} s (before: {item.old}). "
                           "Nothing is rolled back; check the device at both addresses.")
            col._seen(target, new_info)
            await col._poll_one(http, {**dev, "host": target})

        item.status = "done"
        store.add_audit(item.device_id, "address", item.old, target, True, f"static, {call['iface']}")
        log.info("Address change %s done: %s -> %s", item.device_id, item.old, target)
