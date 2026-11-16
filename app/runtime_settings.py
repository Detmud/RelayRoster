"""Settings from the UI.

The environment variables provide the defaults. What is changed in the UI is stored as an override in
the database (`meta.settings` under `/data`) and applies immediately, without restart or configuration change.
`null` when saving resets a value to the default from the environment.

Deliberately only via the environment: `OIDC_*` (otherwise you could lock yourself out), `DATA_DIR`, `LOG_LEVEL`.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass
from typing import Any

from . import discovery
from .config import Settings

log = logging.getLogger(__name__)

META_KEY = "settings"


@dataclass(frozen=True)
class Field:
    key: str
    env: str
    kind: str            # "list" | "int" | "float" | "bool" | "password"
    label: str
    help: str
    group: str
    min: float | None = None
    max: float | None = None
    unit: str = ""


FIELDS: tuple[Field, ...] = (
    Field("shelly_password", "SHELLY_PASSWORD", "password", "Device password",
          "Digest auth password (user admin) of protected Shellies. Never shown again after saving.", "Devices"),
    Field("allow_firmware_update", "ALLOW_FIRMWARE_UPDATE", "bool", "Allow firmware updates",
          "Lets RelayRoster start firmware updates (stable or beta channel). Devices restart during the update.",
          "Devices"),
    Field("firmware_parallel", "FIRMWARE_PARALLEL", "int", "Parallel firmware updates",
          "Devices updated at the same time in one job. Every device is unavailable for about a minute while it "
          "restarts; 1 updates one device after the other.", "Devices", 1, 16),
    Field("allow_reboot", "ALLOW_REBOOT", "bool", "Allow restarting devices",
          "Lets RelayRoster restart a device from its detail view (Shelly.Reboot).", "Devices"),
    Field("allow_set_password", "ALLOW_SET_PASSWORD", "bool", "Allow setting device passwords",
          "Lets RelayRoster set the device password above on Shellies without a password (Shelly.SetAuth). "
          "Anything else that talks to these devices then needs the password too.", "Devices"),
    Field("allow_config", "ALLOW_CONFIG", "bool", "Allow config changes",
          "Lets RelayRoster write settings from the Settings tab of a device page (<Component>.SetConfig). "
          "Wi-Fi client, passwords and calibration values stay read-only.", "Devices"),
    Field("allow_address", "ALLOW_ADDRESS", "bool", "Allow address changes",
          "Lets RelayRoster give a device a static IP address from the address plan (network in the policy). "
          "Anything that addresses the device by IP needs the new address afterwards. There is no rollback.", "Devices"),
    Field("scan_networks", "SCAN_NETWORKS", "list", "Networks to scan",
          "IPv4 networks in CIDR notation, one per line. Largest network: /16.", "Discovery"),
    Field("extra_hosts", "EXTRA_HOSTS", "list", "Extra hosts",
          "Fixed addresses or host names outside the scanned networks, one per line.", "Discovery"),
    Field("scan_every_n_polls", "SCAN_EVERY_N_POLLS", "int", "Scan every n-th sync",
          "0 = never scan automatically, only with the Scan button.", "Discovery", 0, 1000),
    Field("scan_concurrency", "SCAN_CONCURRENCY", "int", "Parallel scan requests",
          "Addresses probed at the same time.", "Discovery", 1, 1024),
    Field("scan_timeout_s", "SCAN_TIMEOUT_SECONDS", "float", "Scan timeout",
          "Wait per address during the scan.", "Discovery", 0.2, 30, "s"),
    Field("poll_interval_min", "POLL_INTERVAL_MINUTES", "int", "Sync interval",
          "Time between two full syncs.", "Sync", 1, 1440, "min"),
    Field("rpc_timeout_s", "RPC_TIMEOUT_SECONDS", "float", "Device timeout",
          "Wait per RPC call to a device.", "Sync", 1, 120, "s"),
    Field("snapshots_keep", "SNAPSHOTS_KEEP", "int", "Backups per device",
          "Older snapshots are deleted.", "Sync", 1, 10000),
    Field("sidebar_folded", "SIDEBAR_FOLDED", "bool", "Compact sidebar",
          "Icon bar that expands on hover. Off: the sidebar always shows the page titles. Desktop only.", "Appearance"),
)
BY_KEY = {f.key: f for f in FIELDS}

_HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")


class SettingsError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,;\s]+", value)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("Expected a list of strings.")
    out: list[str] = []
    for v in (v.strip() for v in value):
        if v and v not in out:
            out.append(v)
    return out


def _clean(f: Field, value: Any) -> Any:
    """Checks and normalises a value. ValueError with a message for the UI on invalid input."""
    if f.kind == "bool":
        if not isinstance(value, bool):
            raise ValueError("Expected true or false.")
        return value
    if f.kind == "password":
        if not isinstance(value, str):
            raise ValueError("Expected text.")
        if len(value) > 256:
            raise ValueError("At most 256 characters.")
        return value
    if f.kind in ("int", "float"):
        if isinstance(value, bool):
            raise ValueError("Expected a number.")
        if isinstance(value, float) and f.kind == "int" and value.is_integer():
            value = int(value)
        try:
            num = float(value) if f.kind == "float" else int(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError("Expected a whole number." if f.kind == "int" else "Expected a number.") from None
        if f.min is not None and num < f.min or f.max is not None and num > f.max:
            lo = f"{f.min:g}" if f.min is not None else "-"
            hi = f"{f.max:g}" if f.max is not None else "-"
            raise ValueError(f"Allowed range: {lo} to {hi}{(' ' + f.unit) if f.unit else ''}.")
        return num
    items = _as_list(value)
    if f.key == "scan_networks":
        plan = discovery.plan_targets(items)
        if plan.warnings:
            raise ValueError(" ".join(plan.warnings))
        return [str(ipaddress.ip_network(i, strict=False)) for i in items]
    if f.key == "extra_hosts":
        bad = []
        for h in items:
            try:
                ipaddress.ip_address(h)
            except ValueError:
                if not _HOST.match(h):
                    bad.append(h)
        if bad:
            raise ValueError(f"Not an IP address or host name: {', '.join(bad)}.")
        return items
    return items


class RuntimeSettings:
    """Manages defaults (environment) and overrides (database) and writes the result into `cfg`."""

    def __init__(self, cfg: Settings, store):
        self.cfg = cfg
        self.store = store
        self.defaults = {f.key: _copy(getattr(cfg, f.key)) for f in FIELDS}
        self.overrides: dict[str, Any] = {}
        stored = store.get_meta(META_KEY, {}) or {}
        for key, value in (stored.items() if isinstance(stored, dict) else []):
            f = BY_KEY.get(key)
            if f is None:
                log.warning("Setting %s unknown, ignored", key)
                continue
            try:
                self.overrides[key] = _clean(f, value)
            except ValueError as exc:
                log.warning("Stored setting %s invalid (%s), the default from the environment applies", key, exc)
        self._apply()

    def _apply(self) -> None:
        for f in FIELDS:
            setattr(self.cfg, f.key, _copy(self.overrides.get(f.key, self.defaults[f.key])))

    def update(self, changes: dict[str, Any]) -> list[str]:
        """Applies changes (None = back to default). All or nothing. Returns the changed keys."""
        if not isinstance(changes, dict):
            raise SettingsError({"_": "Expected an object."})
        errors: dict[str, str] = {}
        new = dict(self.overrides)
        for key, value in changes.items():
            f = BY_KEY.get(key)
            if f is None:
                errors[key] = "Unknown setting."
                continue
            if value is None:
                new.pop(key, None)
                continue
            try:
                cleaned = _clean(f, value)
            except ValueError as exc:
                errors[key] = str(exc)
                continue
            if cleaned == self.defaults[key]:
                new.pop(key, None)       # equal to the default: no override needed
            else:
                new[key] = cleaned
        if errors:
            raise SettingsError(errors)
        before = {f.key: getattr(self.cfg, f.key) for f in FIELDS}
        self.overrides = new
        self.store.set_meta(META_KEY, new)
        self._apply()
        changed = [f.key for f in FIELDS if getattr(self.cfg, f.key) != before[f.key]]
        if changed:
            log.info("Settings changed: %s", ", ".join(changed))
        return changed

    def view(self) -> dict[str, Any]:
        """For the UI. The device password never leaves the server, only whether one is set."""
        fields = []
        for f in FIELDS:
            item: dict[str, Any] = {
                "key": f.key, "env": f.env, "kind": f.kind, "label": f.label, "help": f.help, "group": f.group,
                "min": f.min, "max": f.max, "unit": f.unit, "overridden": f.key in self.overrides,
            }
            if f.kind == "password":
                item["value"] = None
                item["is_set"] = bool(getattr(self.cfg, f.key))
                item["default_is_set"] = bool(self.defaults[f.key])
            else:
                item["value"] = getattr(self.cfg, f.key)
                item["default"] = self.defaults[f.key]
            fields.append(item)
        return {"fields": fields, "env_only": ["OIDC_*", "DATA_DIR", "LOG_LEVEL"]}


def _copy(v: Any) -> Any:
    return list(v) if isinstance(v, list) else v
