"""Views for the Profiles tab: list with counters, selection helpers and live preview."""
from __future__ import annotations

import re
from typing import Any

from . import compare, policy as pol
from .models import device_type, model_name

_COMPONENT = re.compile(r"^([a-z0-9_]+):\d+(?=\.|$)")
MAX_PATHS = 2000


BASELINE = "\0baseline"   # key in summary(), cannot clash with a profile name


def generalize(path: str) -> str:
    """cover:0.power_limit -> cover:*.power_limit (suggestion with wildcard per component)."""
    return _COMPONENT.sub(r"\1:*", path)


def device_row(d: dict[str, Any]) -> dict[str, Any]:
    return {"id": d["id"], "name": d.get("name"), "host": d.get("host"), "app": d.get("app"),
            "model": d.get("model"), "model_name": model_name(d.get("model"), d.get("app"), d.get("gen")),
            "profile": d.get("profile"), "gen": d.get("gen")}


def choices(devices: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Values actually present per match field, with counts. app and model with product name."""
    out: dict[str, dict[str, dict[str, Any]]] = {k: {} for k in ("kind", "variant", "app", "model", "profile", "gen",
                                                                 "name", "id")}
    for d in devices:
        dtype = device_type(d.get("model"), d.get("app"), d.get("gen"))
        label = dtype["model_name"]
        for key in out:
            val = dtype[key] if key in ("kind", "variant") else d.get(key)
            if val in (None, ""):
                continue
            val = str(val)
            entry = out[key].setdefault(val, {"value": val, "count": 0})
            entry["count"] += 1
            if key in ("app", "model"):
                entry["label"] = label
    return {k: sorted(v.values(), key=lambda e: (-e["count"], e["value"])) for k, v in out.items()}


def summary(policy_data: dict[str, Any] | None, devices: list[dict[str, Any]],
            findings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{profile name: {devices: [ids], findings: n}} from the stored policy."""
    out: dict[str, dict[str, Any]] = {}
    if not policy_data:
        return out
    for prof in policy_data["profiles"]:
        name = prof.get("name")
        ids = [d["id"] for d in devices if pol.profile_applies(prof, d)]
        src = f"Profile: {name}"
        out[name] = {"devices": ids, "findings": sum(1 for f in findings if f.get("source") == src)}
    out[BASELINE] = {"devices": [d["id"] for d in devices],
                     "findings": sum(1 for f in findings if f.get("source") == "Baseline")}
    return out


def preview(policy_data: dict[str, Any], prof: dict[str, Any], devices: list[dict[str, Any]],
            configs: dict[str, dict[str, Any]], baseline: bool = False) -> dict[str, Any]:
    """Matching devices, actual value per rule and device, and the deviations this profile creates.

    baseline: prof is the baseline ({settings}), it applies to every device.
    """
    matched = list(devices) if baseline else [d for d in devices if pol.profile_applies(prof, d)]
    src = "Baseline" if baseline else f"Profile: {prof['name']}"
    rules = []
    for pattern, expected in (prof.get("settings") or {}).items():
        per_dev = []
        for d in matched:
            flat = compare.flatten(configs.get(d["id"]) or {})
            if d["id"] not in configs:
                per_dev.append({"id": d["id"], "state": "nodata", "values": []})
                continue
            if not compare.component_exists(pattern, flat):
                per_dev.append({"id": d["id"], "state": "na", "values": []})
                continue
            paths = compare.expand_pattern(pattern, flat)
            if not paths:
                per_dev.append({"id": d["id"], "state": "missing", "values": []})
                continue
            vals = [{"path": p, "actual": flat[p], "ok": pol.value_ok(expected, flat[p])} for p in paths]
            per_dev.append({"id": d["id"], "state": "ok" if all(v["ok"] for v in vals) else "bad", "values": vals})
        rules.append({"path": pattern, "expected": pol.describe_expected(expected), "devices": per_dev})

    findings = []
    paths: set[str] = set()
    for d in matched:
        config = configs.get(d["id"])
        if config is None:
            continue
        for f in pol.evaluate(policy_data, d, config, d.get("status"), {}):
            if f.get("source") == src:
                findings.append({**f, "device_id": d["id"], "device_name": d.get("name")})
        for p in compare.flatten(config):
            paths.add(generalize(p))
    hidden = tuple(compare.READ_ONLY_PATHS) + tuple(compare.MANAGED_PATHS)
    suggest = sorted(p for p in paths if not compare.matches_any(hidden, p))[:MAX_PATHS]
    return {"devices": [device_row(d) for d in matched], "rules": rules, "findings": findings, "paths": suggest}
