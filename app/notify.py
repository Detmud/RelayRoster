"""Notifications via ntfy or webhook (issue #11).

Events:
- ``offline`` / ``online``: a device failed ``notify_offline_after`` syncs in a row; back online is only sent after
  offline was sent (against flapping Wi-Fi and restarts during firmware updates)
- ``new_device``: a scan found a device with an unknown ID and MAC. The first inventory (empty database) is not sent.
- ``drift``: a finding (policy, firmware, naming plan, …) that the previous sync did not have. The first sync after
  start or after enabling only records the state.
- ``write_failed``: every failed entry in the audit log of a device. Failures within ``batch_s`` are sent together.

Offline, new devices and drift are evaluated once at the end of each sync and sent as one message. The state lives
in ``meta`` (``notify:state``), so a restart does not repeat messages. Nothing here talks to a Shelly: the only
requests go to the configured ntfy and webhook URLs.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Iterable

import httpx

from .config import Settings
from .store import Store, now_iso
from .version import VERSION

log = logging.getLogger(__name__)

STATE_KEY = "notify:state"
LAST_KEY = "notify:last"

# Event type -> setting that switches it on
EVENT_SETTING = {
    "offline": "notify_offline",
    "online": "notify_offline",
    "new_device": "notify_new_device",
    "drift": "notify_drift",
    "write_failed": "notify_write_failed",
}
# Order in the message and the ntfy tag of each type
ORDER = ("offline", "write_failed", "drift", "new_device", "online")
TAGS = {"offline": "red_circle", "online": "green_circle", "new_device": "new",
        "drift": "triangular_flag_on_post", "write_failed": "warning"}
HEADINGS = {"offline": "Offline", "online": "Back online", "new_device": "New device",
            "drift": "New deviation", "write_failed": "Write action failed"}
MAX_LINES = 30     # per message; the rest is counted
TIMEOUT_S = 10.0


def _label(name: str | None, device_id: str) -> str:
    return f"{name} ({device_id})" if name else device_id


def _finding_key(f: dict[str, Any]) -> str:
    return "|".join(str(f.get(k) or "") for k in ("device_id", "kind", "source", "path"))


def _event_line(e: dict[str, Any]) -> str:
    who = _label(e.get("device_name"), e.get("device_id") or "")
    t = e["type"]
    if t == "offline":
        return f"{who} at {e.get('host')}: no answer for {e.get('failed_syncs')} syncs ({e.get('error') or 'error'})"
    if t == "online":
        return f"{who} at {e.get('host')} answers again"
    if t == "new_device":
        return f"{who} at {e.get('host')} ({e.get('model') or e.get('app') or 'unknown model'})"
    if t == "drift":
        exp = f", expected {e['expected']}" if e.get("expected") not in (None, "") else ""
        act = f", is {e['actual']}" if e.get("actual") not in (None, "") else ""
        return f"{who}: {e.get('label') or e.get('path')} ({e.get('source')}{exp}{act})"
    if t == "write_failed":
        return f"{who}: {e.get('action')} failed: {e.get('detail') or 'no details'}"
    return who


def render(events: list[dict[str, Any]]) -> tuple[str, str]:
    """Title and text of one message."""
    events = sorted(events, key=lambda e: ORDER.index(e["type"]) if e["type"] in ORDER else 99)
    types = [t for t in ORDER if any(e["type"] == t for e in events)]
    if len(events) == 1:
        title = f"RelayRoster: {HEADINGS.get(events[0]['type'], events[0]['type'])}"
    else:
        parts = [f"{sum(e['type'] == t for e in events)} × {HEADINGS[t].lower()}" for t in types]
        title = "RelayRoster: " + ", ".join(parts)
    lines: list[str] = []
    for t in types:
        group = [e for e in events if e["type"] == t]
        lines.append(f"{HEADINGS[t]}:")
        lines += [f"- {_event_line(e)}" for e in group]
    if len(lines) > MAX_LINES:
        rest = len(lines) - MAX_LINES + 1
        lines = lines[:MAX_LINES - 1] + [f"… and {rest} more lines"]
    return title, "\n".join(lines)


class Notifier:
    """Collects events, keeps the state across syncs and sends to ntfy and/or the webhook."""

    def __init__(self, store: Store, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.store = store
        self.settings = settings
        self.transport = transport
        self.batch_s = 10.0                     # window in which write failures are collected
        self._pending: list[dict[str, Any]] = []
        self._plock = threading.Lock()
        self._flush_handle: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task] = set()
        store.on_audit = self.audit_entry

    # ---- Settings ------------------------------------------------------------------------

    def targets(self) -> list[str]:
        return [t for t, url in (("ntfy", self.settings.notify_ntfy_url), ("webhook", self.settings.notify_webhook_url))
                if url]

    def active(self) -> bool:
        return bool(self.targets())

    def wants(self, event_type: str) -> bool:
        return self.active() and bool(getattr(self.settings, EVENT_SETTING[event_type], False))

    def status(self) -> dict[str, Any]:
        """For the Settings page: configured targets and the result of the last delivery."""
        return {"targets": self.targets(), "last": self.store.get_meta(LAST_KEY)}

    def _state(self) -> dict[str, Any]:
        st = self.store.get_meta(STATE_KEY) or {}
        return st if isinstance(st, dict) else {}

    # ---- Evaluation at the end of a sync ----------------------------------------------------

    def after_run(self, results: dict[str, bool], new_devices: Iterable[dict[str, Any]],
                  findings: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """results: device_id -> the device answered in this sync (an RPC error such as a missing password still
        counts as an answer). findings: from report.build, None = not evaluated.

        Updates the state and sends one message. Returns the events that were sent (switched-off types are left out).
        """
        st = self._state()
        fails: dict[str, int] = {k: int(v) for k, v in (st.get("fails") or {}).items()}
        offline: set[str] = set(st.get("offline") or [])
        devices = {d["id"]: d for d in self.store.devices()}
        events: list[dict[str, Any]] = []

        for dev_id, ok in results.items():
            dev = devices.get(dev_id) or {"id": dev_id}
            base = {"device_id": dev_id, "device_name": dev.get("name"), "host": dev.get("host")}
            if ok:
                fails.pop(dev_id, None)
                if dev_id in offline:
                    offline.discard(dev_id)
                    events.append({"type": "online", **base})
                continue
            fails[dev_id] = fails.get(dev_id, 0) + 1
            if fails[dev_id] >= max(1, int(self.settings.notify_offline_after)) and dev_id not in offline:
                offline.add(dev_id)
                events.append({"type": "offline", **base, "failed_syncs": fails[dev_id],
                               "error": dev.get("last_error")})
        # forgotten devices drop out of the state
        fails = {k: v for k, v in fails.items() if k in devices}
        offline &= set(devices)

        for d in new_devices:
            events.append({"type": "new_device", "device_id": d["id"], "device_name": d.get("name"),
                           "host": d.get("host"), "model": d.get("model"), "app": d.get("app"), "gen": d.get("gen")})

        seen = st.get("findings")
        if findings is None or not self.wants("drift"):
            new_seen = None        # off: the next evaluation starts with a fresh baseline instead of a flood
        else:
            new_seen = sorted({_finding_key(f) for f in findings})
            if seen is not None:
                old = set(seen)
                done: set[str] = set()
                for f in findings:
                    key = _finding_key(f)
                    if key in old or key in done:
                        continue
                    done.add(key)
                    events.append({"type": "drift", "device_id": f.get("device_id"),
                                   "device_name": f.get("device_name"), "kind": f.get("kind"),
                                   "source": f.get("source"), "path": f.get("path"), "label": f.get("label"),
                                   "expected": _short(f.get("expected")), "actual": _short(f.get("actual"))})

        self.store.set_meta(STATE_KEY, {"fails": fails, "offline": sorted(offline), "findings": new_seen})
        events = [e for e in events if self.wants(e["type"])]
        with self._plock:            # write failures waiting for their window go along
            events += self._pending
            self._pending = []
            self._cancel_flush()
        if events:
            self.send(events)
        return events

    # ---- Write failures from the audit log ---------------------------------------------

    def audit_entry(self, device_id: str, action: str, old: str | None, new: str | None, ok: bool,
                    detail: str | None) -> None:
        """Hook of Store.add_audit. Only failed actions on devices count."""
        if ok or not device_id or not self.wants("write_failed"):
            return
        dev = self.store.device(device_id) or {}
        event = {"type": "write_failed", "device_id": device_id, "device_name": dev.get("name"),
                 "host": dev.get("host"), "action": action, "detail": detail, "at": now_iso()}
        with self._plock:
            self._pending.append(event)
            if self._flush_handle is None:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return   # no loop here (sync code): goes along with the next flush or sync
                self._flush_handle = loop.call_later(self.batch_s, self._flush_soon)

    def _cancel_flush(self) -> None:
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None

    def _flush_soon(self) -> None:
        with self._plock:
            self._flush_handle = None
            events, self._pending = self._pending, []
        if events:
            self.send(events)

    async def flush(self) -> None:
        """Sends waiting write failures now and waits for all deliveries (tests, shutdown)."""
        with self._plock:
            self._cancel_flush()
            events, self._pending = self._pending, []
        if events:
            self.send(events)
        await self.drain()

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ---- Delivery ---------------------------------------------------------------------------

    def send(self, events: list[dict[str, Any]]) -> None:
        """In the background: a slow ntfy server never holds up a sync."""
        task = asyncio.get_running_loop().create_task(self.deliver(events))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def deliver(self, events: list[dict[str, Any]], test: bool = False) -> dict[str, Any]:
        """Sends one message to every configured target. Returns {target: {"ok", "error"}}."""
        title, text = render(events) if events else ("RelayRoster: test", "Test notification from RelayRoster.")
        urgent = any(e["type"] in ("offline", "write_failed") for e in events)
        tags = sorted({TAGS[e["type"]] for e in events if e["type"] in TAGS}) or ["bell"]
        results: dict[str, dict[str, Any]] = {}
        async with httpx.AsyncClient(transport=self.transport, timeout=TIMEOUT_S, follow_redirects=False,
                                     trust_env=True) as http:
            if self.settings.notify_ntfy_url:
                headers = {"Authorization": f"Bearer {self.settings.notify_ntfy_token}"} \
                    if self.settings.notify_ntfy_token else {}
                # Title and tags as query parameters: device names may contain umlauts, headers must be Latin-1
                params = {"title": title, "tags": ",".join(tags), "priority": "4" if urgent else "3"}
                results["ntfy"] = await self._post(http, self.settings.notify_ntfy_url, params=params,
                                                   content=text.encode("utf-8"),
                                                   headers={**headers, "Content-Type": "text/plain; charset=utf-8"})
            if self.settings.notify_webhook_url:
                body = {"source": "relayroster", "version": VERSION, "at": now_iso(), "test": test,
                        "title": title, "text": text, "events": events}
                results["webhook"] = await self._post(http, self.settings.notify_webhook_url, json=body)
        if results:
            self.store.set_meta(LAST_KEY, {"at": now_iso(), "test": test, "events": len(events),
                                           "results": results})
        return results

    async def _post(self, http: httpx.AsyncClient, url: str, **kw: Any) -> dict[str, Any]:
        try:
            r = await http.post(url, **kw)
        except httpx.HTTPError as exc:
            msg = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            log.warning("Notification to %s failed: %s", _safe_url(url), msg)
            return {"ok": False, "error": msg}
        if r.status_code >= 300:
            log.warning("Notification to %s failed: HTTP %s", _safe_url(url), r.status_code)
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        return {"ok": True, "error": None}

    async def stop(self) -> None:
        await self.flush()


def _short(value: Any) -> Any:
    """Expected/actual values for the message: scalars as they are, everything else as short text."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    return text if len(text) <= 80 else text[:77] + "…"


def _safe_url(url: str) -> str:
    """For the log: scheme and host only. Topic and webhook IDs are secrets."""
    try:
        u = httpx.URL(url)
        return f"{u.scheme}://{u.host}"
    except Exception:
        return "?"
