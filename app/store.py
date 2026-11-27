"""SQLite storage: devices, versioned snapshots, audit log of write actions, metadata."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id            TEXT PRIMARY KEY,
    host          TEXT NOT NULL,
    mac           TEXT,
    name          TEXT,
    app           TEXT,
    model         TEXT,
    gen           INTEGER,
    ver           TEXT,
    fw_id         TEXT,
    profile       TEXT,
    auth_en       INTEGER,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT,
    last_ok       TEXT,
    last_error    TEXT,
    status_json   TEXT,
    last_snapshot INTEGER
);
CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id    TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    taken_at     TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    hash         TEXT NOT NULL,
    ver          TEXT,
    data_json    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_device ON snapshots(device_id, id DESC);
-- Every write action on a device, including failed ones. Kept when a device is forgotten.
CREATE TABLE IF NOT EXISTS audit (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    device_id TEXT NOT NULL,
    action    TEXT NOT NULL,
    old       TEXT,
    new       TEXT,
    ok        INTEGER NOT NULL,
    detail    TEXT
);
CREATE INDEX IF NOT EXISTS audit_device ON audit(device_id, id DESC);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self.db.commit()
        # Called after every audit entry with the same arguments (notifications about failed write actions)
        self.on_audit: Callable[[str, str, str | None, str | None, bool, str | None], None] | None = None

    # ---- Devices -------------------------------------------------------------------------------

    def device_seen(self, host: str, info: dict[str, Any]) -> list[str]:
        """From scan or read: create the device or update address/master data.

        Returns the IDs under which RelayRoster listed the same device (same MAC) so far, otherwise [].
        """
        ts = now_iso()
        with self._lock:
            replaced = self._adopt_renamed(info)
            self.db.execute(
                """
                INSERT INTO devices (id, host, mac, name, app, model, gen, ver, fw_id, profile, auth_en,
                                     first_seen, last_seen)
                VALUES (:id, :host, :mac, :name, :app, :model, :gen, :ver, :fw_id, :profile, :auth_en, :ts, :ts)
                ON CONFLICT(id) DO UPDATE SET
                    host=excluded.host, mac=excluded.mac,
                    name=COALESCE(excluded.name, devices.name),
                    app=excluded.app, model=excluded.model, gen=excluded.gen, ver=excluded.ver,
                    fw_id=excluded.fw_id, profile=COALESCE(excluded.profile, devices.profile),
                    auth_en=excluded.auth_en, last_seen=excluded.last_seen
                """,
                {
                    "id": info["id"],
                    "host": host,
                    "mac": info.get("mac"),
                    "name": info.get("name"),
                    "app": info.get("app"),
                    "model": info.get("model"),
                    "gen": info.get("gen"),
                    "ver": info.get("ver"),
                    "fw_id": info.get("fw_id"),
                    "profile": info.get("profile"),
                    "auth_en": 1 if info.get("auth_en") else 0,
                    "ts": ts,
                },
            )
            self.db.commit()
        return replaced

    _COPY_COLS = ("host, mac, name, app, model, gen, ver, fw_id, profile, auth_en, first_seen, last_seen, last_ok, "
                  "last_error, status_json, last_snapshot")

    def _adopt_renamed(self, info: dict[str, Any]) -> list[str]:
        """Same MAC, different ID = the same device under a new ID.

        Shelly firmware 2.x renames devices (`shelly1pmminig3-<mac>` becomes `shellymini1pmg3-<mac>`). Snapshots and
        audit move to the new ID, the old entry disappears. Also applies if a scan already created the new ID
        as a device of its own. Runs in the transaction of device_seen (no commit here).
        """
        new_id, mac = info["id"], info.get("mac")
        if not mac:
            return []
        old_ids = [r["id"] for r in self.db.execute(
            "SELECT id FROM devices WHERE mac IS NOT NULL AND upper(mac)=upper(?) AND id<>?", (str(mac), new_id)
        ).fetchall()]
        for old_id in old_ids:
            if self.db.execute("SELECT 1 FROM devices WHERE id=?", (new_id,)).fetchone():
                self.db.execute(
                    "UPDATE devices SET first_seen=min(first_seen, (SELECT first_seen FROM devices WHERE id=?)) "
                    "WHERE id=?", (old_id, new_id))
            else:
                self.db.execute(
                    f"INSERT INTO devices (id, {self._COPY_COLS}) SELECT ?, {self._COPY_COLS} FROM devices WHERE id=?",
                    (new_id, old_id))
            self.db.execute("UPDATE snapshots SET device_id=? WHERE device_id=?", (new_id, old_id))
            self.db.execute("UPDATE audit SET device_id=? WHERE device_id=?", (new_id, old_id))
            self.db.execute("DELETE FROM meta WHERE key=?", (f"methods:{old_id}",))   # read again
            self.db.execute("DELETE FROM devices WHERE id=?", (old_id,))
            self.db.execute(
                "INSERT INTO audit (at, device_id, action, old, new, ok, detail) VALUES (?,?,?,?,?,1,?)",
                (now_iso(), new_id, "device_id", old_id, new_id, "Device reports a new ID (same MAC), history kept"))
        return old_ids

    def renamed_to(self, device_id: str) -> str | None:
        """New ID of a device that renamed itself, otherwise None."""
        with self._lock:
            row = self.db.execute(
                "SELECT new FROM audit WHERE action='device_id' AND old=? ORDER BY id DESC LIMIT 1", (device_id,)
            ).fetchone()
        return row["new"] if row else None

    def record_snapshot(self, device_id: str, snap: dict[str, Any], digest: str, keep: int) -> tuple[int, bool]:
        """Creates a snapshot if the target state changed. Otherwise only
        `confirmed_at` of the last snapshot is updated. Returns (snapshot_id, changed)."""
        ts = now_iso()
        info = snap.get("info") or {}
        status = snap.get("status") or {}
        stored = {k: snap.get(k) for k in ("info", "config", "scripts", "schedules", "webhooks", "kvs")}
        with self._lock:
            last = self.db.execute(
                "SELECT id, hash FROM snapshots WHERE device_id=? ORDER BY id DESC LIMIT 1", (device_id,)
            ).fetchone()
            if last and last["hash"] == digest:
                snap_id, changed = last["id"], False
                self.db.execute("UPDATE snapshots SET confirmed_at=?, ver=? WHERE id=?", (ts, info.get("ver"), snap_id))
            else:
                cur = self.db.execute(
                    "INSERT INTO snapshots (device_id, taken_at, confirmed_at, hash, ver, data_json) VALUES (?,?,?,?,?,?)",
                    (device_id, ts, ts, digest, info.get("ver"), json.dumps(stored, ensure_ascii=False)),
                )
                snap_id, changed = int(cur.lastrowid), True
            name = ((snap.get("config") or {}).get("sys") or {}).get("device", {}).get("name") or info.get("name")
            self.db.execute(
                """UPDATE devices SET last_ok=?, last_seen=?, last_error=NULL, status_json=?, last_snapshot=?,
                                     name=COALESCE(?, name), ver=?, fw_id=?, profile=?, auth_en=?
                   WHERE id=?""",
                (
                    ts, ts, json.dumps(status, ensure_ascii=False), snap_id, name,
                    info.get("ver"), info.get("fw_id"), info.get("profile"),
                    1 if info.get("auth_en") else 0, device_id,
                ),
            )
            if keep > 0:
                self.db.execute(
                    """DELETE FROM snapshots WHERE device_id=? AND id NOT IN
                       (SELECT id FROM snapshots WHERE device_id=? ORDER BY id DESC LIMIT ?)""",
                    (device_id, device_id, keep),
                )
            self.db.commit()
        return snap_id, changed

    def record_error(self, device_id: str, error: str) -> None:
        with self._lock:
            self.db.execute("UPDATE devices SET last_error=? WHERE id=?", (error[:500], device_id))
            self.db.commit()

    def forget_device(self, device_id: str) -> bool:
        """Removes a device from RelayRoster only (e.g. uninstalled). The device itself stays untouched."""
        with self._lock:
            self.db.execute("DELETE FROM snapshots WHERE device_id=?", (device_id,))
            cur = self.db.execute("DELETE FROM devices WHERE id=?", (device_id,))
            self.db.commit()
            return cur.rowcount > 0

    def devices(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM devices ORDER BY app, name, id").fetchall()
        return [self._device_row(r) for r in rows]

    def device(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        return self._device_row(row) if row else None

    @staticmethod
    def _device_row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["status"] = json.loads(d.pop("status_json") or "null")
        d["auth_en"] = bool(d.get("auth_en"))
        return d

    # ---- Snapshots ----------------------------------------------------------------------------

    def snapshot(self, snap_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self.db.execute("SELECT * FROM snapshots WHERE id=?", (snap_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["data"] = json.loads(d.pop("data_json"))
        return d

    def latest_snapshot(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.db.execute(
                "SELECT id FROM snapshots WHERE device_id=? ORDER BY id DESC LIMIT 1", (device_id,)
            ).fetchone()
        return self.snapshot(row["id"]) if row else None

    def snapshots(self, device_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute(
                "SELECT id, device_id, taken_at, confirmed_at, hash, ver FROM snapshots WHERE device_id=? ORDER BY id DESC",
                (device_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- Audit-Log ----------------------------------------------------------------------------

    def add_audit(self, device_id: str, action: str, old: str | None, new: str | None, ok: bool,
                  detail: str | None = None) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO audit (at, device_id, action, old, new, ok, detail) VALUES (?,?,?,?,?,?,?)",
                (now_iso(), device_id, action, old, new, 1 if ok else 0, (detail or "")[:500] or None),
            )
            self.db.commit()
        if self.on_audit is not None:
            try:
                self.on_audit(device_id, action, old, new, ok, (detail or "")[:500] or None)
            except Exception:   # a notification never breaks the write action itself
                logging.getLogger(__name__).exception("Audit hook failed")

    def audit(self, device_id: str | None = None, limit: int = 50, action: str | None = None) -> list[dict[str, Any]]:
        """Newest first. device_id "" = entries without device (Settings, policy), None = all."""
        where, args = [], []
        if device_id is not None:
            where.append("device_id=?")
            args.append(device_id)
        if action:
            where.append("action=?")
            args.append(action)
        sql = "SELECT * FROM audit" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self.db.execute(sql, (*args, limit)).fetchall()
        return [{**dict(r), "ok": bool(r["ok"])} for r in rows]

    def audit_actions(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self.db.execute("SELECT DISTINCT action FROM audit ORDER BY action").fetchall()]

    # ---- Metadaten ----------------------------------------------------------------------------

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self.db.commit()

    # ---- RPC methods per device ------------------------------------------------------------

    def set_methods(self, device_id: str, ver: str | None, methods: list[str] | None) -> None:
        """Result of Shelly.ListMethods per firmware version. methods None = device does not know ListMethods."""
        self.set_meta(f"methods:{device_id}", {"ver": ver, "methods": methods})

    def methods_entry(self, device_id: str) -> dict[str, Any] | None:
        m = self.get_meta(f"methods:{device_id}")
        return m if isinstance(m, dict) else None

    def device_methods(self, device_id: str) -> set[str] | None:
        """Known methods or None (unknown: then everything counts as supported). Lower case, because
        ListMethods returns `Wifi.SetConfig`/`Mqtt.SetConfig`, while the docs write `WiFi`/`MQTT`."""
        m = (self.methods_entry(device_id) or {}).get("methods")
        return {str(x).lower() for x in m} if isinstance(m, list) else None

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default
