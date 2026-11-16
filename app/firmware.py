"""Firmware updates: single or for several devices, always one after the other as a background job.

Flow per device:
1. Check identity: this device must answer at the stored address
2. `Shelly.CheckForUpdate`: get the current target version for the channel. No update available or device
   already on the target version → do nothing (idempotent)
3. `Shelly.Update {"stage": …}`. If the connection drops, the device has already started the update
4. Up to `timeout_s`, `Shelly.GetDeviceInfo` every `poll_s` seconds. Success as soon as `ver` or `fw_id`
   changed. Then read everything again (new snapshot) and record it in the audit log

Only one job runs at a time. Cancelling prevents further devices from starting, a running update finishes.
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
    status: str = "queued"     # queued, checking, updating, rebooting, done, up_to_date, failed, skipped
    error: str | None = None
    started: float | None = None
    duration_s: int | None = None

    def view(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("device_id", "name", "host", "old", "target", "new", "status",
                                              "error", "duration_s")}


@dataclass
class Job:
    id: int
    stage: str
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
        current = next((it for it in self.items if it.status in ("checking", "updating", "rebooting")), None)
        return {
            "id": self.id, "stage": self.stage, "running": self.running, "cancelled": self.cancelled,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "total": len(self.items), "counts": counts,
            "done": sum(1 for it in self.items if it.status not in ("queued", "checking", "updating", "rebooting")),
            "current": current.view() if current else None,
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
        self.job = Job(id=next(_job_ids), stage=stage, items=items)
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
                    item.status = "skipped"
                    item.error = "Cancelled before start"
                    continue
                item.started = time.monotonic()
                try:
                    await self._update_one(item, job.stage)
                except FirmwareError as exc:
                    item.status, item.error = "failed", str(exc)
                except Exception as exc:  # never leave the job with half-set state
                    log.exception("Firmware update %s: unexpected error", item.device_id)
                    item.status, item.error = "failed", f"{type(exc).__name__}: {exc}"
                item.duration_s = int(time.monotonic() - item.started)
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
            item.status = "checking"
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
                item.status, item.new = "up_to_date", item.old
                return

            item.status = "updating"
            log.info("Firmware-Update %s: %s -> %s (%s)", item.device_id, item.old, item.target, stage)
            try:
                await client.update_firmware(stage)
            except Exception as exc:
                if not _starting_update(exc):
                    raise fail(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__) from exc
                log.info("Firmware update %s: connection dropped at the start (%s), device is restarting",
                         item.device_id, type(exc).__name__)

            item.status = "rebooting"
            deadline = time.monotonic() + self.timeout_s
            new_info: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                await asyncio.sleep(self.poll_s)
                try:
                    info = await client.call("Shelly.GetDeviceInfo")
                except (httpx.HTTPError, RpcError):
                    continue      # still restarting
                if (info or {}).get("ver") != item.old or (old_fw and info.get("fw_id") != old_fw):
                    new_info = info
                    break
            if new_info is None:
                raise fail(f"Device did not report a new firmware within {int(self.timeout_s)} s "
                           f"(still {item.old}). Check the device web UI.")
            item.new = new_info.get("ver")
            await col._poll_one(http, dev)

        if not store.device(item.device_id):   # device reports a new ID after the update
            item.device_id = store.renamed_to(item.device_id) or item.device_id
        item.status = "done"
        note = None if item.new == item.target else f"expected {item.target}"
        store.add_audit(item.device_id, "firmware", item.old, item.new, True, note)
        log.info("Firmware-Update %s erfolgreich: %s -> %s", item.device_id, item.old, item.new)
