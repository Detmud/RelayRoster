"""Scan and read all devices, periodically and on demand."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import compare, devicepage, discovery, policy as pol, policy_edit, writable
from .config import Settings
from .apply import ApplyRunner
from .firmware import FirmwareUpdater, _starting_update
from .shelly import RpcError, ShellyClient, WriteBlocked, clean_name, content_hash
from .store import Store, now_iso

log = logging.getLogger(__name__)


class RenameError(RuntimeError):
    """Renaming failed. The message is meant for the UI."""


class RebootError(RuntimeError):
    """Restart failed or was not confirmed. The message is meant for the UI."""


class AuthError(RuntimeError):
    """Setting the device password failed or was not confirmed. Message for the UI."""


class ConfigError(RuntimeError):
    """Writing the configuration failed or was not confirmed. Message for the UI."""

    def __init__(self, msg: str, result: dict[str, Any] | None = None):
        super().__init__(msg)
        self.result = result or {}


class ConfigInvalid(ValueError):
    """Draft invalid, before any network access. errors: {path: message}."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def _same(a: Any, b: Any) -> bool:
    """Same value; numbers compared numerically (1 == 1.0), otherwise exactly as in the comparison."""
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b)):
        return abs(a - b) < 1e-9
    return compare.canon(a) == compare.canon(b)


def _fmt(value: Any) -> str:
    """Value for the audit log: JSON, so on/off, number and text stay distinguishable."""
    return json.dumps(value, ensure_ascii=False)


@dataclass
class RunState:
    running: bool = False
    phase: str = "idle"
    done: int = 0
    total: int = 0
    found: int = 0                       # devices the running scan has found so far
    phase_started: float | None = None   # time.monotonic() at the start of the phase, for the remaining time
    cancelling: bool = False
    last_started: str | None = None
    last_finished: str | None = None
    last_scan: str | None = None
    last_summary: dict[str, Any] = field(default_factory=dict)
    last_error: str | None = None

    def eta_seconds(self) -> int | None:
        """Remaining time of the running phase from the pace so far, None while there is too little data."""
        if not self.running or self.phase_started is None or self.done < 1 or self.total <= self.done:
            return None
        elapsed = time.monotonic() - self.phase_started
        if elapsed < 2:
            return None
        return int(elapsed / self.done * (self.total - self.done))

    def elapsed_seconds(self) -> int | None:
        if not self.running or self.phase_started is None:
            return None
        return int(time.monotonic() - self.phase_started)


def _clock() -> float:
    """Own clock for the wait time, so tests can change it without the event loop."""
    return time.monotonic()


class Collector:
    def __init__(self, store: Store, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.store = store
        self.settings = settings
        self.transport = transport
        self.state = RunState(last_scan=store.get_meta("last_scan"), last_finished=store.get_meta("last_poll"))
        self._lock = asyncio.Lock()
        self._polls = 0
        self._task: asyncio.Task | None = None
        self._cancel = asyncio.Event()
        self._wake = asyncio.Event()
        # Check configuration errors in SCAN_NETWORKS, the UI shows them
        self.plan = discovery.plan_targets(settings.scan_networks, settings.extra_hosts)
        self.firmware = FirmwareUpdater(self)
        self.apply = ApplyRunner(self)    # apply profile, bulk change
        # Restart: how often and how long RelayRoster asks until the device answers again
        self.reboot_poll_s = 2.0
        self.reboot_timeout_s = 90.0

    def settings_changed(self) -> None:
        """After changes in the UI: plan scan targets again, recalculate the wait time with the new interval."""
        self.plan = discovery.plan_targets(self.settings.scan_networks, self.settings.extra_hosts)
        self._wake.set()

    def _http(self) -> httpx.AsyncClient:
        limits = httpx.Limits(max_connections=max(8, self.settings.scan_concurrency), max_keepalive_connections=16)
        return httpx.AsyncClient(transport=self.transport, limits=limits, follow_redirects=False, trust_env=False)

    def cancel_scan(self) -> bool:
        """Cancels a running network scan. Devices found so far stay, the read follows."""
        if not (self.state.running and self.state.phase == "scan"):
            return False
        self._cancel.set()
        self.state.cancelling = True
        return True

    async def run(self, scan: bool = False, auto_scan: bool = True) -> dict[str, Any]:
        """One full run. If one is already running, no second one starts in parallel.

        Without known devices the run scans by itself, unless auto_scan is False
        (SCAN_EVERY_N_POLLS=0: scans only on demand).
        """
        if self._lock.locked():
            return {"skipped": True}
        async with self._lock:
            st = self.state
            st.running, st.last_error = True, None
            st.last_started = now_iso()
            summary = {"scanned": False, "found": 0, "polled": 0, "changed": 0, "errors": 0, "cancelled": False}
            try:
                async with self._http() as http:
                    if scan or (auto_scan and not self.store.devices()):
                        self._cancel.clear()
                        st.phase, st.done, st.total, st.found = "scan", 0, self.plan.total, 0
                        st.phase_started, st.cancelling = time.monotonic(), False

                        def progress(done: int, total: int, found: int) -> None:
                            st.done, st.total, st.found = done, total, found

                        def seen(host: str, info: dict[str, Any]) -> None:
                            # save immediately: a cancelled scan loses nothing it found
                            self._seen(host, info)

                        found = await discovery.scan(
                            http, self.plan, concurrency=self.settings.scan_concurrency,
                            timeout=self.settings.scan_timeout_s, progress=progress, on_found=seen,
                            cancel=self._cancel,
                        )
                        cancelled = self._cancel.is_set() and st.done < st.total
                        summary.update(scanned=True, found=len(found), cancelled=cancelled)
                        if not cancelled:
                            st.last_scan = now_iso()
                            self.store.set_meta("last_scan", st.last_scan)

                    devices = self.store.devices()
                    st.phase, st.done, st.total = "poll", 0, len(devices)
                    st.phase_started, st.cancelling = time.monotonic(), False
                    sem = asyncio.Semaphore(8)

                    async def poll(dev: dict[str, Any]) -> None:
                        async with sem:
                            ok, changed = await self._poll_one(http, dev)
                        summary["polled"] += 1
                        summary["changed"] += int(changed)
                        summary["errors"] += int(not ok)
                        st.done += 1

                    await asyncio.gather(*(poll(d) for d in devices))
                self._polls += 1
            except Exception as exc:  # never let the background loop die
                log.exception("Run failed")
                st.last_error = str(exc)
            finally:
                st.running, st.phase, st.cancelling, st.phase_started = False, "idle", False, None
                st.last_finished = now_iso()
                st.last_summary = summary
                self.store.set_meta("last_poll", st.last_finished)
            return summary

    async def _poll_one(self, http: httpx.AsyncClient, dev: dict[str, Any]) -> tuple[bool, bool]:
        client = ShellyClient(http, dev["host"], self.settings.shelly_password, self.settings.rpc_timeout_s)
        try:
            snap = await client.snapshot()
        except (httpx.HTTPError, RpcError, ValueError, KeyError) as exc:
            msg = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            self.store.record_error(dev["id"], msg)
            log.warning("%s (%s): %s", dev["id"], dev["host"], msg)
            return False, False
        info = snap.get("info") or {}
        replaced = self._seen(dev["host"], info) if info.get("id") else []
        if info.get("id") != dev["id"]:
            if dev["id"] not in replaced:
                # DHCP reassigned the address: a different device now answers at this IP
                self.store.record_error(
                    dev["id"],
                    f"{dev['host']} now answers as {info.get('id')}. The next scan will look for this device again."
                )
                return False, False
            # Same device (same MAC) under a new ID: keep reading under the new ID
            dev = self.store.device(info["id"]) or dev
        _, changed = self.store.record_snapshot(dev["id"], snap, content_hash(snap), self.settings.snapshots_keep)
        await self._read_methods(client, dev["id"], info.get("ver"))
        return True, changed

    def _seen(self, host: str, info: dict[str, Any]) -> list[str]:
        """Store a device. If it reports a new ID (same MAC), policy.yaml follows:
        overrides and profile assignments are keyed by ID."""
        replaced = self.store.device_seen(host, info)
        for old_id in replaced:
            new_id = info["id"]
            log.warning("%s now reports as %s, history taken over", old_id, new_id)
            try:
                text = pol.read_text(self.settings.policy_path)
                new_text = re.sub(rf"(?<![\w-]){re.escape(old_id)}(?![\w-])", lambda _m: new_id, text)
                if new_text != text:
                    policy_edit.save_text(self.settings.policy_path, new_text)   # with backup
                    self.store.add_audit("", "policy", old_id, new_id, True, "device ID replaced in policy.yaml")
            except Exception as exc:  # policy missing, invalid or not writable: take over the device anyway
                log.warning("policy.yaml: %s not replaced by %s: %s", old_id, new_id, exc)
        return replaced

    async def poll_device(self, device_id: str) -> dict[str, Any]:
        """Read only this device again, e.g. from the device page. Also runs next to a full run."""
        dev = self.store.device(device_id)
        if not dev:
            raise KeyError(device_id)
        async with self._http() as http:
            ok, changed = await self._poll_one(http, dev)
        return {"ok": ok, "changed": changed,
                "error": None if ok else (self.store.device(device_id) or {}).get("last_error")}

    async def _read_methods(self, client: ShellyClient, device_id: str, ver: str | None) -> None:
        """Read Shelly.ListMethods once per firmware version. Errors leave the capabilities unknown."""
        known = self.store.methods_entry(device_id)
        if known and known.get("ver") == ver:
            return
        try:
            res = await client.call_optional("Shelly.ListMethods")
        except (httpx.HTTPError, RpcError, ValueError):
            return
        methods = res.get("methods") if isinstance(res, dict) else None
        self.store.set_methods(device_id, ver, sorted(map(str, methods)) if isinstance(methods, list) else None)

    def supports(self, device_id: str, method: str) -> bool:
        """False only if ListMethods is known and the method is missing."""
        methods = self.store.device_methods(device_id)
        return methods is None or method.lower() in methods

    # ---- Umbenennen ---------------------------------------------------------------------

    async def rename(self, device_id: str, new_name: str) -> dict[str, Any]:
        """Writes sys.device.name to the device and confirms it by reading again.

        Before writing, RelayRoster checks that this device really answers at the stored address
        (DHCP can swap addresses). Afterwards it reads the whole device again; the new state
        is stored as a snapshot in the backup history. Every attempt is in the audit log.
        """
        dev = self.store.device(device_id)
        if not dev:
            raise KeyError(device_id)
        name = clean_name(new_name)            # ValueError for an invalid name, before any network access
        old = dev.get("name")
        if old == name:
            return {"ok": True, "changed": False, "old": old, "new": name, "restart_required": False}

        def fail(msg: str) -> RenameError:
            self.store.add_audit(device_id, "rename", old, name, False, msg)
            log.warning("Renaming %s failed: %s", device_id, msg)
            return RenameError(msg)

        async with self._http() as http:
            client = ShellyClient(http, dev["host"], self.settings.shelly_password, self.settings.rpc_timeout_s)
            try:
                info = await client.call("Shelly.GetDeviceInfo")
                if (info or {}).get("id") != device_id:
                    raise fail(f"{dev['host']} answers as {(info or {}).get('id')}, not {device_id}. "
                               "Run a network scan first.")
                res = await client.set_name(name)
            except RenameError:
                raise
            except (httpx.HTTPError, RpcError, WriteBlocked, ValueError) as exc:
                raise fail(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__) from exc
            ok, _ = await self._poll_one(http, dev)

        now = (self.store.device(device_id) or {}).get("name")
        if not ok or now != name:
            raise fail(f"Device did not confirm the new name (reads back {now!r}).")
        self.store.add_audit(device_id, "rename", old, name, True,
                             "restart required" if res.get("restart_required") else None)
        log.info("Umbenannt: %s %r -> %r", device_id, old, name)
        return {"ok": True, "changed": True, "old": old, "new": name,
                "restart_required": bool(res.get("restart_required"))}

    async def set_auth(self, device_id: str) -> dict[str, Any]:
        """Sets the password from Settings on a device without password.

        Only open devices: RelayRoster does not know any other existing password. Check identity first (as
        when renaming), read again afterwards; confirmed once the device reports auth_en and answers with the
        password. The password itself never appears in the audit log.
        """
        dev = self.store.device(device_id)
        if not dev:
            raise KeyError(device_id)
        password = self.settings.shelly_password
        if not password:
            raise AuthError("Set the device password in Settings first.")
        if dev.get("auth_en"):
            return {"ok": True, "changed": False}

        def fail(msg: str) -> AuthError:
            self.store.add_audit(device_id, "auth", None, None, False, msg)
            log.warning("Setting password on %s failed: %s", device_id, msg)
            return AuthError(msg)

        if not self.supports(device_id, "Shelly.SetAuth"):
            raise fail("This device does not offer Shelly.SetAuth.")

        async with self._http() as http:
            client = ShellyClient(http, dev["host"], password, self.settings.rpc_timeout_s)
            try:
                info = await client.call("Shelly.GetDeviceInfo") or {}
                if info.get("id") != device_id:
                    raise fail(f"{dev['host']} answers as {info.get('id')}, not {device_id}. Run a network scan first.")
                if not info.get("auth_en"):
                    await client.set_auth(device_id, password)
            except AuthError:
                raise
            except (httpx.HTTPError, RpcError, WriteBlocked, ValueError) as exc:
                raise fail(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__) from exc
            ok, _ = await self._poll_one(http, dev)
        if not ok or not (self.store.device(device_id) or {}).get("auth_en"):
            raise fail("Device did not confirm the password.")
        self.store.add_audit(device_id, "auth", None, None, True, "password set")
        log.info("Password set: %s", device_id)
        return {"ok": True, "changed": True}

    # ---- Edit configuration -------------------------------------------------------------

    def config_plan(self, device_id: str, changes: Any) -> dict[str, Any]:
        """Checks a draft against the last snapshot and builds the SetConfig calls. Writes nothing.

        Preview and write use the same function, so the preview shows exactly the calls that are sent
        afterwards. Unchanged values drop out.
        """
        dev = self.store.device(device_id)
        if not dev:
            raise KeyError(device_id)
        if not isinstance(changes, dict) or not changes:
            raise ConfigInvalid({"": "No changes"})
        snap = self.store.latest_snapshot(device_id)
        flat = compare.flatten(((snap or {}).get("data") or {}).get("config") or {})
        errors: dict[str, str] = {}
        rows: list[dict[str, Any]] = []
        clean: dict[str, Any] = {}
        for path, value in changes.items():
            if not isinstance(path, str) or not writable.is_editable(path):
                errors[str(path)] = "cannot be changed here"
                continue
            if path not in flat:
                errors[path] = "this device does not have this setting"
                continue
            meta = devicepage.field_meta(path, flat[path])
            try:
                new = writable.validate(path, value, meta, flat[path])
            except ValueError as exc:
                errors[path] = str(exc)
                continue
            if _same(new, flat[path]):
                continue
            clean[path] = new
            rows.append({"path": path, "label": meta["label"], "unit": meta.get("unit"), "old": flat[path],
                         "new": new})
        if errors:
            raise ConfigInvalid(errors)
        calls = writable.build_calls(clean)
        for call in calls:
            if not writable.is_config_call(call["method"], call["params"]):   # double check
                raise ConfigInvalid({p: "cannot be changed here" for p in call["paths"]})
            if not self.supports(device_id, call["method"]):
                raise ConfigInvalid({p: f"this device does not offer {call['method']}" for p in call["paths"]})
        rows.sort(key=lambda r: r["path"])
        return {"device_id": device_id, "rows": rows, "calls": calls, "changes": clean,
                "snapshot_id": (snap or {}).get("id")}

    async def set_config(self, device_id: str, changes: Any) -> dict[str, Any]:
        """Writes a draft to the device and confirms every value by reading again.

        Same flow as renaming: check identity, read the current configuration and compare it with the snapshot
        (if something changed on the device in the meantime, RelayRoster aborts), one SetConfig per component,
        then read again, snapshot, audit entry per path. An error in one component stops the following ones.
        """
        plan = self.config_plan(device_id, changes)
        dev = self.store.device(device_id) or {}
        rows = {r["path"]: r for r in plan["rows"]}
        if not rows:
            return {"ok": True, "changed": False, "results": [], "restart_required": False}

        def audit(path: str, ok: bool, note: str | None = None) -> None:
            r = rows[path]
            self.store.add_audit(device_id, "config", _fmt(r["old"]), _fmt(r["new"]), ok,
                                 f"{path} · {note}" if note else path)

        def fail_all(msg: str) -> ConfigError:
            for path in rows:
                audit(path, False, msg)
            log.warning("Configuration %s failed: %s", device_id, msg)
            return ConfigError(msg)

        def err(exc: Exception) -> str:
            return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__

        results: dict[str, dict[str, Any]] = {}
        restart = False
        async with self._http() as http:
            client = ShellyClient(http, dev["host"], self.settings.shelly_password, self.settings.rpc_timeout_s)
            try:
                info = await client.call("Shelly.GetDeviceInfo")
                if (info or {}).get("id") != device_id:
                    raise fail_all(f"{dev['host']} answers as {(info or {}).get('id')}, not {device_id}. "
                                   "Run a network scan first.")
                live = compare.flatten(await client.call("Shelly.GetConfig") or {})
            except ConfigError:
                raise
            except (httpx.HTTPError, RpcError, WriteBlocked, ValueError) as exc:
                raise fail_all(err(exc)) from exc
            moved = [p for p, r in rows.items() if not _same(live.get(p), r["old"])]
            if moved:
                raise fail_all(f"Changed on the device since the last read ({', '.join(moved)}). "
                               "Reload the page and check the values.")

            stop: str | None = None
            for call in plan["calls"]:
                if stop:
                    for p in call["paths"]:
                        results[p] = {"path": p, "ok": False, "error": "not sent: " + stop}
                    continue
                try:
                    res = await client.set_config(call["method"], call["params"])
                except (httpx.HTTPError, RpcError, WriteBlocked, ValueError) as exc:
                    stop = f"{call['method']} failed"
                    for p in call["paths"]:
                        results[p] = {"path": p, "ok": False, "error": err(exc)}
                    continue
                need = bool((res or {}).get("restart_required"))
                restart = restart or need
                for p in call["paths"]:
                    results[p] = {"path": p, "ok": None, "restart_required": need}
            ok, _ = await self._poll_one(http, dev)

        snap = self.store.latest_snapshot(device_id)
        now = compare.flatten(((snap or {}).get("data") or {}).get("config") or {}) if ok else {}
        for path, r in results.items():
            if r["ok"] is None:
                r["ok"] = ok and path in now and _same(now[path], rows[path]["new"])
                if not r["ok"]:
                    r["error"] = (f"Device reads back {now.get(path)!r}" if ok
                                  else "Could not read the device after writing")
            note = r.get("error") or ("restart required" if r.get("restart_required") else None)
            audit(path, bool(r["ok"]), note)
        out = [results[p] for p in sorted(results)]
        failed = [r for r in out if not r["ok"]]
        summary = {"ok": not failed, "changed": True, "results": out, "restart_required": restart}
        if failed:
            log.warning("Configuration %s: %d of %d values not confirmed", device_id, len(failed), len(out))
            raise ConfigError(f"{len(failed)} of {len(out)} settings were not confirmed by the device: "
                              + "; ".join(f"{r['path']}: {r['error']}" for r in failed), summary)
        log.info("Configuration %s written: %s", device_id, ", ".join(sorted(results)))
        return summary

    # ---- Restart ------------------------------------------------------------------------

    async def reboot(self, device_id: str) -> dict[str, Any]:
        """Restarts a device and waits until it answers again.

        Before the restart RelayRoster checks the identity (as when renaming) and remembers the uptime.
        The restart is confirmed as soon as the device answers again and reports a lower uptime.
        Afterwards RelayRoster reads the whole device again. Every attempt is in the audit log.
        """
        dev = self.store.device(device_id)
        if not dev:
            raise KeyError(device_id)

        def fail(msg: str) -> RebootError:
            self.store.add_audit(device_id, "reboot", None, None, False, msg)
            log.warning("Restart %s failed: %s", device_id, msg)
            return RebootError(msg)

        if not self.supports(device_id, "Shelly.Reboot"):
            raise fail("This device does not offer Shelly.Reboot.")

        def err(exc: Exception) -> str:
            return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__

        async with self._http() as http:
            client = ShellyClient(http, dev["host"], self.settings.shelly_password, self.settings.rpc_timeout_s)
            try:
                info = await client.call("Shelly.GetDeviceInfo")
                if (info or {}).get("id") != device_id:
                    raise fail(f"{dev['host']} answers as {(info or {}).get('id')}, not {device_id}. "
                               "Run a network scan first.")
                before = (await client.call("Sys.GetStatus") or {}).get("uptime")
            except RebootError:
                raise
            except (httpx.HTTPError, RpcError, WriteBlocked, ValueError) as exc:
                raise fail(err(exc)) from exc

            started = time.monotonic()
            log.info("Restart %s (%s), uptime before %s s", device_id, dev["host"], before)
            try:
                await client.reboot()
            except Exception as exc:
                # If the connection drops, the device is already restarting (as with a firmware update)
                if not _starting_update(exc):
                    raise fail(err(exc)) from exc

            seen_down = False
            uptime = None
            deadline = started + self.reboot_timeout_s
            while time.monotonic() < deadline:
                await asyncio.sleep(self.reboot_poll_s)
                try:
                    uptime = (await client.call("Sys.GetStatus") or {}).get("uptime")
                except (httpx.HTTPError, RpcError):
                    seen_down = True
                    continue
                # Lower uptime than before = restarted. Without uptime it is enough that it was gone and is back
                if (isinstance(before, (int, float)) and isinstance(uptime, (int, float)) and uptime < before) \
                        or (seen_down and not isinstance(before, (int, float))):
                    break
            else:
                raise fail(f"Device did not confirm a restart within {int(self.reboot_timeout_s)} s. "
                           "Check its Wi-Fi signal and power.")
            seconds = int(time.monotonic() - started)
            ok, _ = await self._poll_one(http, dev)

        self.store.add_audit(device_id, "reboot", None, None, True, f"back after {seconds} s")
        log.info("Restart %s confirmed after %s s (uptime %s s)", device_id, seconds, uptime)
        return {"ok": True, "seconds": seconds, "uptime": uptime, "read": ok}

    # ---- Hintergrund-Loop ---------------------------------------------------------------------

    async def loop(self) -> None:
        await asyncio.sleep(2)
        while True:
            every = self.settings.scan_every_n_polls
            if every > 0:
                await self.run(scan=self._polls % every == 0)
            else:
                await self.run(scan=False, auto_scan=False)   # scans only on demand
            await self._wait_interval()

    async def _wait_interval(self) -> None:
        """Waits for the next run. If the interval changes, the new value applies from the last run."""
        started = _clock()
        while True:
            remaining = max(1, self.settings.poll_interval_min) * 60 - (_clock() - started)
            if remaining <= 0:
                return
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.loop())

    async def stop(self) -> None:
        await self.firmware.stop()
        await self.apply.stop()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
