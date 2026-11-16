"""Firmware updates: single or for several devices as a background job. Several devices are updated at the
same time, at most `firmware_parallel` (Settings, default 4; 1 = one after the other). The limit keeps the load
on the Wi-Fi and the vendor's servers low and leaves most of the house usable while devices restart.

Flow per device, each step is a status the UI shows:
1. `checking`: identity (this device must answer at the stored address), then `Shelly.CheckForUpdate` for the
   target version of the channel. No update available or already on the target version → `up_to_date`
2. `requested`: `Shelly.Update {"stage": …}` sent. If the connection drops, the device has already started
3. Up to `timeout_s`, `Shelly.GetDeviceInfo` every `poll_s` seconds:
   `installing` while the device still answers with the old version (it downloads and writes the firmware),
   `rebooting` while it does not answer
4. `verifying`: the device is back with a new `ver` or `fw_id`; read everything again (new snapshot)
5. `done`, recorded in the audit log

Only one job runs at a time. Cancelling prevents further devices from starting, running updates finish.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from .shelly import FIRMWARE_STAGES, RpcError, ShellyClient, WriteBlocked
from .store import now_iso

if TYPE_CHECKING:
    from .collector import Collector

log = logging.getLogger(__name__)

# Error codes a device answers with while it is going into the update restart
_STARTING_CODES = {-109, -32900}
_job_ids = itertools.count(1)
PARALLEL_MAX = 16


class FirmwareError(RuntimeError):
    """Update failed. The message is meant for the UI."""


def _starting_update(exc: Exception) -> bool:
    """True if the error of the Shelly.Update call means: the device has already started."""
    if isinstance(exc, (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError)):
        return True
    if isinstance(exc, RpcError):
        msg = str(exc).lower()
        return exc.code in _STARTING_CODES or any(w in msg for w in ("shutting down", "closed", "timeout"))
    return False


@dataclass
class Item:
    device_id: str
    name: str | None
    host: str
    old: str | None = None
    target: str | None = None
    new: str | None = None
    # queued, checking, requested, installing, rebooting, verifying, done, up_to_date, failed, skipped
    status: str = "queued"
    error: str | None = None
    started: float | None = None
    duration_s: int | None = None
    # Steps passed so far with the seconds since the start of this device: [["checking", 0], ["requested", 2], …]
    phases: list[list[Any]] = field(default_factory=list)

    def set(self, status: str) -> None:
        """Enter a step; the time line keeps every step once."""
        if status == self.status:
            return
        self.status = status
        self.phases.append([status, int(time.monotonic() - self.started) if self.started else 0])

    def view(self) -> dict[str, Any]:
        out = {k: getattr(self, k) for k in ("device_id", "name", "host", "old", "target", "new", "status",
                                             "error", "duration_s", "phases")}
        if self.started is not None and self.duration_s is None:
            out["elapsed_s"] = int(time.monotonic() - self.started)
        return out


ACTIVE = ("checking", "requested", "installing", "rebooting", "verifying")


@dataclass
class Job:
    id: int
    stage: str
    items: list[Item]
    started_at: str = field(default_factory=now_iso)
    finished_at: str | None = None
    cancelled: bool = False
    parallel: int = 1

    @property
    def running(self) -> bool:
        return self.finished_at is None

    def view(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for it in self.items:
            counts[it.status] = counts.get(it.status, 0) + 1
        active = [it for it in self.items if it.status in ACTIVE]
        return {
            "id": self.id, "stage": self.stage, "running": self.running, "cancelled": self.cancelled,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "total": len(self.items), "counts": counts, "parallel": self.parallel,
            "done": sum(1 for it in self.items if it.status != "queued" and it.status not in ACTIVE),
            "active": len(active),
            "current": active[0].view() if active else None,
            "items": [it.view() for it in self.items],
        }


class FirmwareUpdater:
    def __init__(self, collector: Collector, poll_s: float = 5.0, timeout_s: float = 180.0):
        self.collector = collector
        self.poll_s = poll_s
        self.timeout_s = timeout_s
        self.job: Job | None = None
        self._task: asyncio.Task | None = None

    # ---- Job-Steuerung --------------------------------------------------------------------------

    def start(self, device_ids: list[str], stage: str = "stable") -> Job:
        if stage not in FIRMWARE_STAGES:
            raise ValueError(f"Unknown channel '{stage}', allowed: {', '.join(FIRMWARE_STAGES)}")
        if self.job and self.job.running:
            raise RuntimeError("A firmware update is already running")
        moving = getattr(self.collector, "address", None)
        if moving and moving.job and moving.job.running:
            raise RuntimeError("Addresses are being changed, wait until this has finished")
        ids = list(dict.fromkeys(device_ids))   # keep the order, drop duplicates
        if not ids:
            raise ValueError("No devices selected")
        store = self.collector.store
        items = []
        for dev_id in ids:
            dev = store.device(dev_id)
            if not dev:
                raise KeyError(dev_id)
            items.append(Item(device_id=dev_id, name=dev.get("name"), host=dev["host"], old=dev.get("ver")))
        parallel = max(1, min(PARALLEL_MAX, int(getattr(self.collector.settings, "firmware_parallel", 1) or 1)))
        self.job = Job(id=next(_job_ids), stage=stage, items=items, parallel=parallel)
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
        slots = asyncio.Semaphore(job.parallel)

        async def one(item: Item) -> None:
            async with slots:      # devices start in the order of the list, job.parallel at a time
                if job.cancelled:
                    item.status = "skipped"
                    item.error = "Cancelled before start"
                    return
                item.started = time.monotonic()
                try:
                    await self._update_one(item, job.stage)
                except FirmwareError as exc:
                    item.error = str(exc)
                    item.set("failed")
                except Exception as exc:  # never leave the job with half-set state
                    log.exception("Firmware update %s: unexpected error", item.device_id)
                    item.error = f"{type(exc).__name__}: {exc}"
                    item.set("failed")
                item.duration_s = int(time.monotonic() - item.started)

        try:
            await asyncio.gather(*(one(item) for item in job.items))
        finally:
            job.finished_at = now_iso()
            log.info("Firmware-Job %s fertig: %s", job.id, job.view()["counts"])

    # ---- one device ------------------------------------------------------------------------------

    async def _update_one(self, item: Item, stage: str) -> None:
        col = self.collector
        store = col.store
        dev = store.device(item.device_id)
        if not dev:
            raise FirmwareError("Device is no longer known to RelayRoster")
        item.host = dev["host"]

        def fail(msg: str) -> FirmwareError:
            store.add_audit(item.device_id, "firmware", item.old, item.target, False, msg)
            log.warning("Firmware-Update %s fehlgeschlagen: %s", item.device_id, msg)
            return FirmwareError(msg)

        if not col.supports(item.device_id, "Shelly.Update"):
            raise fail("This device does not offer Shelly.Update.")

        async with col._http() as http:
            client = ShellyClient(http, dev["host"], col.settings.shelly_password, col.settings.rpc_timeout_s)
            item.set("checking")
            try:
                info = await client.call("Shelly.GetDeviceInfo")
                if (info or {}).get("id") != item.device_id:
                    raise fail(f"{dev['host']} answers as {(info or {}).get('id')}, not {item.device_id}. "
                               "Run a network scan first.")
                item.old = info.get("ver") or item.old
                old_fw = info.get("fw_id")
                offers = await client.check_for_update()
            except FirmwareError:
                raise
            except (httpx.HTTPError, RpcError, WriteBlocked, ValueError) as exc:
                raise fail(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__) from exc

            item.target = ((offers or {}).get(stage) or {}).get("version")
            if not item.target or item.target == item.old:
                item.new = item.old
                item.set("up_to_date")
                return

            log.info("Firmware-Update %s: %s -> %s (%s)", item.device_id, item.old, item.target, stage)
            try:
                await client.update_firmware(stage)
            except Exception as exc:
                if not _starting_update(exc):
                    raise fail(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__) from exc
                log.info("Firmware update %s: connection dropped at the start (%s), device is restarting",
                         item.device_id, type(exc).__name__)
            item.set("requested")

            deadline = time.monotonic() + self.timeout_s
            new_info: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                await asyncio.sleep(self.poll_s)
                try:
                    info = await client.call("Shelly.GetDeviceInfo")
                except (httpx.HTTPError, RpcError):
                    item.set("rebooting")
                    continue
                if (info or {}).get("ver") != item.old or (old_fw and info.get("fw_id") != old_fw):
                    new_info = info
                    break
                if item.status == "requested":      # still answers with the old version: downloading, writing
                    item.set("installing")
            if new_info is None:
                raise fail(f"Device did not report a new firmware within {int(self.timeout_s)} s "
                           f"(still {item.old}). Check the device web UI.")
            item.new = new_info.get("ver")
            item.set("verifying")
            await col._poll_one(http, dev)

        if not store.device(item.device_id):   # device reports a new ID after the update
            item.device_id = store.renamed_to(item.device_id) or item.device_id
        item.set("done")
        note = None if item.new == item.target else f"expected {item.target}"
        store.add_audit(item.device_id, "firmware", item.old, item.new, True, note)
        log.info("Firmware-Update %s erfolgreich: %s -> %s", item.device_id, item.old, item.new)
