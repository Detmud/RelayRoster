"""Comparison logic: flatten configurations, match paths, peer comparison, snapshot diff."""
from __future__ import annotations

import json
import re
from collections import Counter
from fnmatch import fnmatchcase
from typing import Any, Iterable

from .models import model_name

MISSING = "__missing__"


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """{'sys': {'device': {'eco_mode': True}}} -> {'sys.device.eco_mode': True}.
    Lists stay leaves, because their order is part of the value."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        if not obj and prefix:
            out[prefix] = {}
        for key, val in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten(val, path))
    else:
        out[prefix] = obj
    return out


def path_matches(pattern: str, path: str) -> bool:
    """Glob per segment: 'cover:*.motor.*' matches 'cover:0.motor.idle_power_thr'.
    A pattern without a wildcard at the end also matches all sub-paths ('wifi.ap' -> 'wifi.ap.ssid')."""
    p_parts = pattern.split(".")
    v_parts = path.split(".")
    if len(v_parts) < len(p_parts):
        return False
    for p, v in zip(p_parts, v_parts):
        if not fnmatchcase(v, p):
            return False
    return True


def matches_any(patterns: Iterable[str], path: str) -> bool:
    return any(path_matches(p, path) for p in patterns)


def expand_pattern(pattern: str, flat: dict[str, Any]) -> list[str]:
    """All concrete paths of a pattern that have exactly the same depth."""
    depth = pattern.count(".") + 1
    return sorted(p for p in flat if p.count(".") + 1 == depth and path_matches(pattern, p))


def component_exists(pattern: str, flat: dict[str, Any]) -> bool:
    """Does the device have any component the pattern targets at all?"""
    head = pattern.split(".", 1)[0]
    return any(fnmatchcase(p.split(".", 1)[0], head) for p in flat)


def canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


# ---- Firmware -------------------------------------------------------------------------------

_VER = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-(\w+))?")


def version_key(ver: str | None) -> tuple:
    if not ver:
        return (-1,)
    m = _VER.search(ver)
    if not m:
        return (-1,)
    major, minor, patch, pre = m.groups()
    # Pre-releases (beta, rc) sort before the final version
    return (int(major), int(minor), int(patch), 0 if pre else 1, pre or "")


# ---- Peer comparison (devices of the same type among each other) ---------------------------

# Read-only: never count in the comparison. Policy, Fix and the device page must never build a
# write call for them. Device knowledge, so it lives in the code and not in the policy.
READ_ONLY_PATHS = (
    "sys.device.mac",
    "sys.device.fw_id",
    "sys.cfg_rev",
    "sys.device.enhanced_security",
    "wifi.sta.is_open",
    "wifi.sta1.is_open",
    "wifi.ap.is_open",
)
# Managed by the device or the calibration: do not compare (grey on the device page). cloud.server is
# deliberately not here but in peer_depends: with the cloud on, a difference should be visible
MANAGED_PATHS = (
    "sys.ui_data",
    "cover:*.obstruction_detection.power_thr",
    "cover:*.maxtime_open",
    "cover:*.maxtime_close",
)
_COND_OPS = {"in", "not"}


def bind_wildcards(pattern: str, path: str) -> str:
    """Replaces the wildcard segments in `pattern` with the segments of `path` at the same position.

    'cover:*.obstruction_detection.enable' with 'cover:1.obstruction_detection.direction'
    -> 'cover:1.obstruction_detection.enable'. So a condition checks the same component as the path.
    """
    p_parts, v_parts = pattern.split("."), path.split(".")
    return ".".join(v_parts[i] if any(c in seg for c in "*?[") and i < len(v_parts) else seg
                    for i, seg in enumerate(p_parts))


def cond_ok(expected: Any, actual: Any) -> bool:
    """Condition for peer_depends: fixed value, {in: [...]} or {not: x}."""
    if isinstance(expected, dict) and expected and set(expected) <= _COND_OPS:
        if "in" in expected and canon(actual) not in {canon(v) for v in expected["in"]}:
            return False
        if "not" in expected and canon(actual) == canon(expected["not"]):
            return False
        return True
    return canon(expected) == canon(actual)


def depends_for(path: str, depends: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in depends
            if matches_any(d.get("paths") or [], path) and not matches_any(d.get("except") or [], path)]


def active_on(rules: list[dict[str, Any]], path: str, flat: dict[str, Any]) -> bool:
    """Does the setting take effect on this device? All conditions of all matching rules must hold.

    If the control field is missing on the device (other firmware, e.g. ble.enable since 2.0), nothing can
    be ruled out: the condition then counts as met.
    """
    for rule in rules:
        for cpath, expected in (rule.get("when") or {}).items():
            actual = flat.get(bind_wildcards(cpath, path), MISSING)
            if actual is not MISSING and not cond_ok(expected, actual):
                return False
    return True


def describe_when(rules: list[dict[str, Any]], path: str) -> str:
    parts = []
    for rule in rules:
        for cpath, expected in (rule.get("when") or {}).items():
            c = bind_wildcards(cpath, path)
            if isinstance(expected, dict) and "in" in expected:
                parts.append(f"{c} in {', '.join(canon(v) for v in expected['in'])}")
            elif isinstance(expected, dict) and "not" in expected:
                parts.append(f"{c} not {canon(expected['not'])}")
            else:
                parts.append(f"{c} = {canon(expected)}")
    return " and ".join(parts)


def peer_groups(devices: list[dict[str, Any]], configs: dict[str, dict[str, Any]], ignore: list[str],
                depends: Iterable[dict[str, Any]] = (),
                device_ignore: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """Groups by (app, profile) and finds settings that differ within the group.

    devices: rows from the store; configs: {device_id: Shelly.GetConfig}
    device_ignore: {device_id: patterns} from ignore of profile/override. This device's value does not count
    for the path; if fewer than two devices remain, there is no comparison.
    depends: rules from peer_depends. A difference only counts if the setting takes effect on at least one
    device (e.g. cloud.server only with cloud.enable). Otherwise, and for READ_ONLY_PATHS/MANAGED_PATHS,
    it is listed under "hidden" with a reason, so nothing disappears silently.
    """
    depends = list(depends)
    device_ignore = device_ignore or {}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for d in devices:
        if d["id"] not in configs:
            continue
        groups.setdefault((d.get("app") or "?", d.get("profile") or ""), []).append(d)

    result: list[dict[str, Any]] = []
    for (app, profile), members in sorted(groups.items()):
        flats = {
            d["id"]: {p: v for p, v in flatten(configs[d["id"]]).items() if not matches_any(ignore, p)}
            for d in members
        }
        diffs: list[dict[str, Any]] = []
        hidden: list[dict[str, Any]] = []
        if len(members) >= 2:
            all_paths = sorted(set().union(*(f.keys() for f in flats.values())))
            for path in all_paths:
                values = {dev_id: f.get(path, MISSING) for dev_id, f in flats.items()
                          if not matches_any(device_ignore.get(dev_id) or [], path)}
                distinct = {canon(v) for v in values.values()}
                if len(values) < 2 or len(distinct) <= 1:
                    continue
                if matches_any(READ_ONLY_PATHS, path):
                    hidden.append({"path": path, "values": values, "reason": "read_only", "note": "Read-only"})
                    continue
                if matches_any(MANAGED_PATHS, path):
                    hidden.append({"path": path, "values": values, "reason": "managed",
                                   "note": "Managed by the device, the cloud or calibration"})
                    continue
                rules = depends_for(path, depends)
                if rules and not any(active_on(rules, path, f) for f in flats.values()):
                    hidden.append({"path": path, "values": values, "reason": "no_effect",
                                   "note": f"No effect: needs {describe_when(rules, path)}"})
                    continue
                counts = Counter(canon(v) for v in values.values())
                top, top_n = counts.most_common(1)[0]
                tie = sum(1 for n in counts.values() if n == top_n) > 1
                majority = None if tie else json.loads(top)
                outliers = [dev_id for dev_id, v in values.items() if tie or canon(v) != top]
                diffs.append({
                    "path": path,
                    "values": values,
                    "majority": majority,
                    "has_majority": not tie,
                    "outliers": outliers,
                })
        versions = sorted({d.get("ver") or "?" for d in members}, key=version_key)
        result.append({
            "app": app,
            "model_name": model_name(members[0].get("model"), app),
            "profile": profile,
            "devices": [{"id": d["id"], "name": d.get("name"), "ver": d.get("ver")} for d in members],
            "versions": versions,
            "diffs": diffs,
            "hidden": hidden,
        })
    return result


# ---- Snapshot-Diff (Backup-Verlauf) ---------------------------------------------------------

def backup_flat(data: dict[str, Any]) -> dict[str, Any]:
    """Makes a stored snapshot comparable. Scripts are addressed by their name,
    so a different ID does not show up as a complete replacement."""
    out: dict[str, Any] = {}
    out.update(flatten(data.get("config") or {}, "config"))
    for s in data.get("scripts") or []:
        key = f"scripts.{s.get('name') or s.get('id')}"
        out[f"{key}.enable"] = s.get("enable")
        out[f"{key}.code"] = s.get("code")
    for job in data.get("schedules") or []:
        out[f"schedules.{job.get('id')}"] = {k: v for k, v in job.items() if k != "id"}
    for hook in data.get("webhooks") or []:
        out[f"webhooks.{hook.get('id')}"] = {k: v for k, v in hook.items() if k != "id"}
    for key, val in (data.get("kvs") or {}).items():
        out[f"kvs.{key}"] = val
    out.pop("config.sys.cfg_rev", None)
    return out


def diff_flat(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for path in sorted(set(old) | set(new)):
        a, b = old.get(path, MISSING), new.get(path, MISSING)
        if canon(a) == canon(b):
            continue
        kind = "added" if a == MISSING else "removed" if b == MISSING else "changed"
        changes.append({"path": path, "kind": kind, "old": None if a == MISSING else a, "new": None if b == MISSING else b})
    return changes
