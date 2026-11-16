"""Apply a profile and change settings on many devices at once.

Two sources, one flow:
- **Apply profile**: all rules of a profile with a fixed target (`=`) on the devices the profile applies to.
  Rules with `in`, `not`, `min`, `max` name no single value and only appear as a hint
- **Bulk change**: one path (also with wildcard, `cover:*.power_limit`) and one value for a selection of devices,
  e.g. `sys.sntp.server` for the whole fleet

Not every Shelly has every setting. A path only becomes a change in the preview if it is in the last
configuration of **this** device and in the allow list `app/writable.py`. All other rows appear with a
reason (not present, not writable, already set) and are never sent.

Writing runs as a background job, one device after the other, via `Collector.set_config`: check identity,
compare the current configuration with the snapshot, SetConfig per component, read again, snapshot, audit per
path. The job skips moving shutters. Restart only if the device requires it and it was confirmed.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from . import compare, devicepage, policy as pol, writable
from .models import model_name
from .shelly import RpcError, ShellyClient, WriteBlocked
from .store import now_iso

if TYPE_CHECKING:
    from .collector import Collector

log = logging.getLogger(__name__)
_job_ids = itertools.count(1)
MOVING = ("opening", "closing")


def _row(path: str, actual: Any, new: Any, status: str, note: str | None = None) -> dict[str, Any]:
    meta = devicepage.field_meta(path, actual if actual is not compare.MISSING else new)
    return {"path": path, "label": meta["label"], "unit": meta.get("unit"),
            "old": None if actual is compare.MISSING else actual, "new": new, "status": status, "note": note}


def _device_head(d: dict[str, Any]) -> dict[str, Any]:
    return {"device_id": d["id"], "name": d.get("name"), "host": d.get("host"),
            "model_name": model_name(d.get("model"), d.get("app"), d.get("gen"))}


def _finish(collector: Collector, d: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Builds the calls from the rows with status 'change', with the same checks as the device page."""
    out = {**_device_head(d), "rows": rows, "changes": {}, "calls": []}
    changes = {r["path"]: r["new"] for r in rows if r["status"] == "change"}
    while changes:
        try:
            plan = collector.config_plan(d["id"], changes)
        except collector_errors() as exc:
            errors = getattr(exc, "errors", {"": str(exc)})
            for r in rows:
                if r["path"] in errors:
                    r["status"], r["note"] = "invalid", errors[r["path"]]
            bad = set(errors) & set(changes)
            if not bad:
                break
            for p in bad:
                changes.pop(p)
            continue
        out["changes"], out["calls"] = plan["changes"], plan["calls"]
        break
    return out


def collector_errors() -> tuple[type[Exception], ...]:
    from .collector import ConfigInvalid
    return (ConfigInvalid,)


def plan_bulk(collector: Collector, path: str, value: Any, device_ids: list[str]) -> list[dict[str, Any]]:
    """Preview for one path and one value on many devices."""
    path = str(path or "").strip()
    if not path:
        raise ValueError("Path is missing")
    store = collector.store
    out = []
    for dev_id in dict.fromkeys(device_ids):
        d = store.device(dev_id)
        if not d:
            raise KeyError(dev_id)
        snap = store.latest_snapshot(dev_id)
        if not snap:
            out.append({**_device_head(d), "rows": [_row(path, compare.MISSING, value, "nodata",
                                                          "No backup yet, run Sync first")],
                        "changes": {}, "calls": []})
            continue
        flat = compare.flatten((snap.get("data") or {}).get("config") or {})
        rows: list[dict[str, Any]] = []
        if not compare.component_exists(path, flat):
            rows.append(_row(path, compare.MISSING, value, "na", "Component not present on this device"))
        else:
            paths = compare.expand_pattern(path, flat)
            if not paths:
                rows.append(_row(path, compare.MISSING, value, "unsupported",
                                 f"Not supported on this device (firmware {d.get('ver') or '?'})"))
            for p in paths:
                actual = flat[p]
                if not writable.is_editable(p):
                    rows.append(_row(p, actual, value, "not_writable", "RelayRoster does not write this setting"))
                elif pol.value_ok(value, actual):
                    rows.append(_row(p, actual, value, "ok", "already set"))
                else:
                    rows.append(_row(p, actual, value, "change"))
        out.append(_finish(collector, d, rows))
    return out


def plan_restore(collector: Collector, snapshot_id: Any) -> list[dict[str, Any]]:
    """Preview: play a configuration from a backup back onto the same device. Shows every path that differs
    between backup and current state; only writable fields are written."""
    store = collector.store
    try:
        snap = store.snapshot(int(snapshot_id))
    except (TypeError, ValueError):
        snap = None
    if not snap:
        raise LookupError(f"Unknown backup {snapshot_id}")
    d = store.device(snap["device_id"])
    if not d:
        raise KeyError(snap["device_id"])
    cur = store.latest_snapshot(d["id"])
    old = compare.flatten((snap.get("data") or {}).get("config") or {})
    now = compare.flatten(((cur or {}).get("data") or {}).get("config") or {})
    rows: list[dict[str, Any]] = []
    for p in sorted(set(old) | set(now)):
        if compare.matches_any(compare.READ_ONLY_PATHS, p) or compare.matches_any(compare.MANAGED_PATHS, p):
            continue
        actual, wanted = now.get(p, compare.MISSING), old.get(p, compare.MISSING)
        if compare.canon(actual) == compare.canon(wanted):
            continue
        if wanted is compare.MISSING:
            rows.append(_row(p, actual, None, "na", "Not in this backup"))
        elif actual is compare.MISSING:
            rows.append(_row(p, actual, wanted, "na", "Setting no longer on this device"))
        elif not writable.is_editable(p):
            rows.append(_row(p, actual, wanted, "not_writable", "RelayRoster does not write this setting"))
        else:
            rows.append(_row(p, actual, wanted, "change"))
    return [_finish(collector, d, rows)]


def plan_profile(collector: Collector, policy_data: dict[str, Any], name: str,
                 device_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Preview for a profile: per device the rules with a fixed target that deviate."""
    prof = next((p for p in policy_data["profiles"] if p.get("name") == name), None)
    if prof is None:
        raise LookupError(f"Profile '{name}' does not exist")
    store = collector.store
    devices = store.devices()
    if device_ids is None:
        device_ids = [d["id"] for d in devices if pol.profile_applies(prof, d)]
    src = f"Profile: {name}"
    out = []
    for dev_id in dict.fromkeys(device_ids):
        d = store.device(dev_id)
        if not d:
            raise KeyError(dev_id)
        snap = store.latest_snapshot(dev_id)
        if not snap:
            out.append({**_device_head(d), "rows": [], "changes": {}, "calls": [],
                        "note": "No backup yet, run Sync first"})
            continue
        flat = compare.flatten((snap.get("data") or {}).get("config") or {})
        concrete, _ = pol.resolve(policy_data, d, flat)
        applies = pol.profile_applies(prof, d)
        rows = []
        for pattern, expected in (prof.get("settings") or {}).items():
            if not compare.component_exists(pattern, flat):
                continue   # e.g. a cover rule on a device without shutter
            paths = compare.expand_pattern(pattern, flat)
            if not paths:
                rows.append(_row(pattern, compare.MISSING, pol.describe_expected(expected), "unsupported",
                                 f"Not supported on this device (firmware {d.get('ver') or '?'})"))
                continue
            for p in paths:
                actual = flat[p]
                if applies and p in concrete and concrete[p][1] != src:
                    rows.append(_row(p, actual, pol.describe_expected(concrete[p][0]), "overridden",
                                     f"{concrete[p][1]} sets this value"))
                    continue
                fixed = devicepage.use_value(expected) if not isinstance(expected, dict) else None
                if pol.value_ok(expected, actual):
                    rows.append(_row(p, actual, pol.describe_expected(expected), "ok", "already set"))
                elif fixed is None:
                    rows.append(_row(p, actual, pol.describe_expected(expected), "hint",
                                     "No single target value; set it per device (Override)"))
                elif not writable.is_editable(p):
                    rows.append(_row(p, actual, fixed, "not_writable", "RelayRoster does not write this setting"))
                else:
                    rows.append(_row(p, actual, fixed, "change"))
        res = _finish(collector, d, rows)
        if not applies:
            res["note"] = "This profile does not apply to this device"
        out.append(res)
    return out


def fleet_paths(devices: list[dict[str, Any]], configs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Writable settings of the fleet for the bulk change page: path with wildcard per component,
    display (type, options, unit) and today's values with counts."""
    from .profiles_view import generalize
    out: dict[str, dict[str, Any]] = {}
    for d in devices:
        flat = compare.flatten(configs.get(d["id"]) or {})
        for p, v in flat.items():
            # Device name: different per device, does not belong in bulk change (the naming plan covers it)
            if not writable.is_editable(p) or p == "sys.device.name":
                continue
            g = generalize(p)
            entry = out.get(g)
            if entry is None:
                meta = devicepage.field_meta(p, v)
                entry = out[g] = {"path": g, "label": meta["label"], "kind": meta["kind"], "unit": meta.get("unit"),
                                  "options": meta.get("options"), "min": meta.get("min"), "max": meta.get("max"),
                                  "devices": set(), "values": {}}
            entry["devices"].add(d["id"])
            key = compare.canon(v)
            val = entry["values"].setdefault(key, {"value": v, "count": 0})
            val["count"] += 1
    rows = []
    for e in out.values():
        e["devices"] = sorted(e["devices"])
        e["values"] = sorted(e["values"].values(), key=lambda x: -x["count"])
        rows.append(e)
    return sorted(rows, key=lambda e: e["path"])


# ---- Job ------------------------------------------------------------------------------------

@dataclass
class Item:
    device_id: str
    name: str | None
    host: str
    changes: dict[str, Any]
    status: str = "queued"    # queued, writing, restarting, done, failed, skipped
    error: str | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    restart_required: bool = False
    restarted: bool = False
    duration_s: int | None = None

    def view(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("device_id", "name", "host", "changes", "status", "error", "results",
                                              "restart_required", "restarted", "duration_s")}


@dataclass
class Job:
    id: int
    title: str
    items: list[Item]
    restart: bool = False
    restart_only: bool = False    # only restart, write nothing
    auth_only: bool = False       # only set the device password
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
        current = next((it for it in self.items if it.status in ("writing", "restarting")), None)
        return {
            "id": self.id, "title": self.title, "running": self.running, "cancelled": self.cancelled,
            "restart": self.restart, "restart_only": self.restart_only, "auth_only": self.auth_only, "started_at": self.started_at, "finished_at": self.finished_at,
            "total": len(self.items), "counts": counts,
            "done": sum(1 for it in self.items if it.status not in ("queued", "writing", "restarting")),
            "current": current.view() if current else None,
            "items": [it.view() for it in self.items],
        }


class ApplyRunner:
    def __init__(self, collector: Collector):
        self.collector = collector
        self.job: Job | None = None
        self._task: asyncio.Task | None = None

    def start(self, items: list[dict[str, Any]], title: str = "Apply settings", restart: bool = False) -> Job:
        if self.job and self.job.running:
            raise RuntimeError("Settings are already being written")
        fw = self.collector.firmware.job
        if fw and fw.running:
            raise RuntimeError("A firmware update is running, wait until it has finished")
        moving = getattr(self.collector, "address", None)
        if moving and moving.job and moving.job.running:
            raise RuntimeError("Addresses are being changed, wait until this has finished")
        store = self.collector.store
        out: list[Item] = []
        seen: set[str] = set()
        for it in items or []:
            dev_id = str((it or {}).get("device_id") or "")
            changes = (it or {}).get("changes")
            if not isinstance(changes, dict) or not changes:
                continue
            if dev_id in seen:
                raise ValueError(f"{dev_id} appears twice")
            seen.add(dev_id)
            d = store.device(dev_id)
            if not d:
                raise KeyError(dev_id)
            self.collector.config_plan(dev_id, changes)   # ConfigInvalid before the start, not in the middle of the job
            out.append(Item(device_id=dev_id, name=d.get("name"), host=d["host"], changes=dict(changes)))
        if not out:
            raise ValueError("Nothing to write")
        self.job = Job(id=next(_job_ids), title=str(title or "Apply settings")[:120], items=out,
                       restart=bool(restart))
        self._task = asyncio.create_task(self._run(self.job))
        return self.job

    def start_auth(self, device_ids: list[str], title: str = "Set device password") -> Job:
        """Sets the device password on the devices one after the other; devices with a password are skipped."""
        job = self.start_restart(device_ids, title, _auth=True)
        return job

    def start_restart(self, device_ids: list[str], title: str = "Restart devices", _auth: bool = False) -> Job:
        """Restarts devices one after the other, e.g. after a bulk change. For each device collector.reboot waits
        until it answers again; moving shutters are skipped."""
        if self.job and self.job.running:
            raise RuntimeError("Settings are already being written")
        fw = self.collector.firmware.job
        if fw and fw.running:
            raise RuntimeError("A firmware update is running, wait until it has finished")
        moving = getattr(self.collector, "address", None)
        if moving and moving.job and moving.job.running:
            raise RuntimeError("Addresses are being changed, wait until this has finished")
        out: list[Item] = []
        for dev_id in dict.fromkeys(str(i) for i in device_ids or []):
            d = self.collector.store.device(dev_id)
            if not d:
                raise KeyError(dev_id)
            out.append(Item(device_id=dev_id, name=d.get("name"), host=d["host"], changes={}))
        if not out:
            raise ValueError("No devices selected")
        self.job = Job(id=next(_job_ids), title=str(title or "Restart devices")[:120], items=out, restart=not _auth,
                       restart_only=not _auth, auth_only=_auth)
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
        from .collector import AuthError, ConfigError, ConfigInvalid, RebootError
        col = self.collector
        try:
            for item in job.items:
                if job.cancelled:
                    item.status, item.error = "skipped", "Cancelled before start"
                    continue
                started = time.monotonic()
                try:
                    moving = await self._moving_covers(item, always=job.restart_only)
                    if moving:
                        item.status = "skipped"
                        item.error = f"Cover {', '.join(map(str, moving))} is moving, try again when it has stopped"
                        continue
                    if job.auth_only:
                        item.status = "writing"
                        res = await col.set_auth(item.device_id)
                        item.status = "done" if res.get("changed") else "skipped"
                        item.error = None if res.get("changed") else "Password already set"
                        continue
                    if job.restart_only:
                        item.status = "restarting"
                        await col.reboot(item.device_id)
                        item.restarted = True
                        item.status = "done"
                        continue
                    item.status = "writing"
                    res = await col.set_config(item.device_id, item.changes)
                    item.results = res.get("results") or []
                    item.restart_required = bool(res.get("restart_required"))
                    if item.restart_required and job.restart and col.settings.allow_reboot:
                        item.status = "restarting"
                        await col.reboot(item.device_id)
                        item.restarted = True
                    item.status = "done"
                except ConfigError as exc:
                    item.status, item.error = "failed", str(exc)
                    item.results = exc.result.get("results") or []
                except (ConfigInvalid, RebootError, AuthError, KeyError) as exc:
                    item.status, item.error = "failed", str(exc)
                except Exception as exc:  # never leave the job with half-set state
                    log.exception("Apply %s: unexpected error", item.device_id)
                    item.status, item.error = "failed", f"{type(exc).__name__}: {exc}"
                finally:
                    item.duration_s = int(time.monotonic() - started)
        finally:
            job.finished_at = now_iso()
            log.info("Anwenden-Job %s fertig: %s", job.id, job.view()["counts"])

    async def _moving_covers(self, item: Item, always: bool = False) -> list[int]:
        """Numbers of the shutters that are moving right now. Only if the job writes cover values or restarts."""
        if not always and not any(p.startswith("cover:") for p in item.changes):
            return []
        col = self.collector
        dev = col.store.device(item.device_id) or {}
        async with col._http() as http:
            client = ShellyClient(http, dev.get("host") or item.host, col.settings.shelly_password,
                                  col.settings.rpc_timeout_s)
            try:
                status = await client.call("Shelly.GetStatus") or {}
            except (httpx.HTTPError, RpcError, WriteBlocked, ValueError):
                return []    # set_config reports reachability itself
        return sorted(int(k.split(":", 1)[1]) for k, v in status.items()
                      if k.startswith("cover:") and isinstance(v, dict) and v.get("state") in MOVING)
