"""Load and validate target profiles (policy.yaml) and evaluate them against devices."""
from __future__ import annotations

import copy
import ipaddress
import os
import tempfile
from fnmatch import fnmatchcase
from typing import Any

import yaml

from .compare import MISSING, canon, component_exists, expand_pattern, flatten, matches_any, version_key
from .shelly import clean_name

DEFAULT_POLICY_PATH = os.path.join(os.path.dirname(__file__), "default_policy.yaml")

_MATCH_KEYS = {"app", "model", "profile", "name", "id", "gen"}
# Allowed keys per profile and override: typos like `setting:` should stand out instead of never taking effect
_PROFILE_KEYS = {"name", "match", "settings", "ignore", "devices", "exclude"}
_OVERRIDE_KEYS = {"settings", "ignore"}
_OPERATORS = {"in", "not", "min", "max"}
_NETWORK_KEYS = {"subnet", "dhcp_pool", "blocks"}
_BLOCK_KEYS = {"name", "match", "range"}
# peer_variants is replaced by `ignore` in profile/override; the key is still accepted
# (existing policies), but no longer evaluated.
# Dependent settings in the peer comparison. A difference in `paths`
# (without `except`) only counts if `when` holds on at least one compared device. Wildcards in `when`
# stand for the same component as in the path (cover:1.… checks cover:1.…). Applies without its own key
# peer_depends; peer_depends: [] turns it off. Must match app/default_policy.yaml (test).
_STATIC_IP = ["ip", "netmask", "gw", "nameserver"]
DEFAULT_PEER_DEPENDS: list[dict[str, Any]] = [
    {"paths": ["cloud.server"], "when": {"cloud.enable": True}},
    {"paths": ["mqtt.*"], "except": ["mqtt.enable"], "when": {"mqtt.enable": True}},
    {"paths": ["ws.server", "ws.ssl_ca"], "when": {"ws.enable": True}},
    {"paths": ["wifi.ap.*"], "except": ["wifi.ap.enable"], "when": {"wifi.ap.enable": True}},
    {"paths": [f"wifi.sta.{k}" for k in _STATIC_IP], "when": {"wifi.sta.ipv4mode": "static"}},
    {"paths": ["wifi.sta1.*"], "except": ["wifi.sta1.enable"], "when": {"wifi.sta1.enable": True}},
    {"paths": [f"wifi.sta1.{k}" for k in _STATIC_IP], "when": {"wifi.sta1.ipv4mode": "static"}},
    {"paths": ["ble.observer.*", "ble.rpc.*"], "when": {"ble.enable": True}},
    {"paths": ["cover:*.obstruction_detection.*"], "except": ["cover:*.obstruction_detection.enable"],
     "when": {"cover:*.obstruction_detection.enable": True}},
    {"paths": ["cover:*.safety_switch.*"], "except": ["cover:*.safety_switch.enable"],
     "when": {"cover:*.safety_switch.enable": True}},
    {"paths": ["cover:*.slat.*"], "except": ["cover:*.slat.enable"], "when": {"cover:*.slat.enable": True}},
    {"paths": ["cover:*.swap_inputs"], "when": {"cover:*.in_mode": {"not": "detached"}}},
    {"paths": ["switch:*.auto_on_delay"], "when": {"switch:*.auto_on": True}},
    {"paths": ["switch:*.auto_off_delay"], "when": {"switch:*.auto_off": True}},
    {"paths": ["switch:*.power_restore_delay"], "when": {"switch:*.power_restore_delay_enable": True}},
    {"paths": ["switch:*.counts.power_thr"], "when": {"switch:*.counts.enable": True}},
    {"paths": ["input:*.*"], "except": ["input:*.enable"], "when": {"input:*.enable": True}},
    {"paths": ["input:*.report_thr", "input:*.range_map", "input:*.range", "input:*.xpercent"],
     "when": {"input:*.type": "analog"}},
    {"paths": ["input:*.count_rep_thr", "input:*.freq_window", "input:*.freq_rep_thr", "input:*.xcounts",
               "input:*.xfreq"], "when": {"input:*.type": "count"}},
    {"paths": ["input:*.invert"], "when": {"input:*.type": {"in": ["switch", "button", "analog"]}}},
    {"paths": ["input:*.factory_reset"], "when": {"input:*.type": {"in": ["switch", "button"]}}},
]
_DEPENDS_KEYS = {"paths", "except", "when"}


class PolicyError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# ---- Loading and validation ----------------------------------------------------------------

def parse(text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise PolicyError([f"YAML error: {exc}"]) from exc
    errors = validate(data)
    if errors:
        raise PolicyError(errors)
    return normalize(data)


def validate(data: Any) -> list[str]:
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["The policy must be a YAML mapping."]
    allowed_top = {"firmware", "security", "cover", "baseline", "profiles", "overrides", "peer_ignore", "peer_variants",
                   "peer_depends", "network", "names"}
    for key in data:
        if key not in allowed_top:
            errs.append(f"Unknown section '{key}' (allowed: {', '.join(sorted(allowed_top))})")

    def check_settings(where: str, settings: Any) -> None:
        if settings is None:
            return
        if not isinstance(settings, dict):
            errs.append(f"{where}: must be a mapping 'path: value'")
            return
        for path, val in settings.items():
            if not isinstance(path, str) or not path:
                errs.append(f"{where}: invalid path {path!r}")
            if isinstance(val, dict) and val and set(val) <= _OPERATORS:
                if "in" in val and not isinstance(val["in"], list):
                    errs.append(f"{where}.{path}: 'in' needs a list")
                for op in ("min", "max"):
                    if op in val and not isinstance(val[op], (int, float)):
                        errs.append(f"{where}.{path}: '{op}' needs a number")

    def check_keys(where: str, level: str, obj: dict[str, Any], allowed: set[str]) -> None:
        for k in obj:
            if k not in allowed:
                errs.append(f"{where}: unknown field '{k}' (allowed: {', '.join(sorted(allowed))})")
        settings = obj.get("settings")
        if isinstance(settings, dict) and "ignore" in settings:
            errs.append(f"{where}.settings: 'ignore' belongs on {level} level, not under settings")

    def check_ignore(where: str, ignore: Any) -> None:
        if ignore is not None and not (isinstance(ignore, list) and all(isinstance(i, str) for i in ignore)):
            errs.append(f"{where}.ignore: must be a list of paths")

    for sec in ("firmware", "security", "cover"):
        if sec in data and data[sec] is not None and not isinstance(data[sec], dict):
            errs.append(f"{sec}: must be a mapping")
    check_settings("baseline", data.get("baseline"))

    profiles = data.get("profiles") or []
    if not isinstance(profiles, list):
        errs.append("profiles: must be a list")
        profiles = []
    for i, prof in enumerate(profiles):
        where = f"profiles[{i}]"
        if not isinstance(prof, dict):
            errs.append(f"{where}: must be a mapping")
            continue
        if not prof.get("name"):
            errs.append(f"{where}: 'name' is missing")
        check_keys(where, "profile", prof, _PROFILE_KEYS)
        match = prof.get("match")
        for key in ("devices", "exclude"):
            ids = prof.get(key)
            if ids is not None and not (isinstance(ids, list) and all(isinstance(i, str) and i for i in ids)):
                errs.append(f"{where}.{key}: must be a list of device IDs")
        if match is None and prof.get("devices"):
            pass   # only assigned devices
        elif not isinstance(match, dict) or not match:
            errs.append(f"{where}: 'match' is missing or empty (or list devices)")
        else:
            for k in match:
                if k not in _MATCH_KEYS:
                    errs.append(f"{where}.match: unknown field '{k}' (allowed: {', '.join(sorted(_MATCH_KEYS))})")
        check_settings(f"{where}.settings", prof.get("settings"))
        check_ignore(where, prof.get("ignore"))

    overrides = data.get("overrides") or {}
    if not isinstance(overrides, dict):
        errs.append("overrides: must be a mapping 'device-id: {...}'")
    else:
        for dev_id, ov in overrides.items():
            if not isinstance(ov, dict):
                errs.append(f"overrides.{dev_id}: must be a mapping")
                continue
            check_keys(f"overrides.{dev_id}", "override", ov, _OVERRIDE_KEYS)
            check_settings(f"overrides.{dev_id}.settings", ov.get("settings"))
            check_ignore(f"overrides.{dev_id}", ov.get("ignore"))

    pi = data.get("peer_ignore")
    if pi is not None and not (isinstance(pi, list) and all(isinstance(i, str) for i in pi)):
        errs.append("peer_ignore: must be a list of paths")
    pv = data.get("peer_variants")
    if pv is not None and not (isinstance(pv, list) and all(isinstance(i, str) for i in pv)):
        errs.append("peer_variants: must be a list of paths")
    errs.extend(_validate_depends(data.get("peer_depends")))
    errs.extend(_validate_network(data.get("network")))
    errs.extend(_validate_names(data.get("names")))
    return errs


def _check_pattern(where: str, pattern: Any) -> str | None:
    """A path pattern: text, no empty levels, square brackets closed."""
    if not isinstance(pattern, str) or not pattern.strip():
        return f"{where}: {pattern!r} is not a path"
    if any(not seg for seg in pattern.split(".")):
        return f"{where}: {pattern!r} has an empty level"
    if pattern.count("[") != pattern.count("]"):
        return f"{where}: {pattern!r} has an unclosed '['"
    return None


def _validate_depends(depends: Any) -> list[str]:
    if depends is None:
        return []
    if not isinstance(depends, list):
        return ["peer_depends: must be a list of rules {paths, except, when}"]
    errs: list[str] = []
    for i, rule in enumerate(depends):
        where = f"peer_depends[{i}]"
        if not isinstance(rule, dict):
            errs.append(f"{where}: must be a mapping {{paths, except, when}}")
            continue
        errs.extend(f"{where}: unknown field '{k}' (allowed: {', '.join(sorted(_DEPENDS_KEYS))})"
                    for k in rule if k not in _DEPENDS_KEYS)
        for key in ("paths", "except"):
            val = rule.get(key)
            if val is None and key == "except":
                continue
            if not isinstance(val, list) or not val:
                errs.append(f"{where}.{key}: must be a non-empty list of paths")
                continue
            errs.extend(e for e in (_check_pattern(f"{where}.{key}", v) for v in val) if e)
        when = rule.get("when")
        if not isinstance(when, dict) or not when:
            errs.append(f"{where}.when: must be a mapping 'path: value'")
            continue
        for cpath, expected in when.items():
            err = _check_pattern(f"{where}.when", cpath)
            if err:
                errs.append(err)
            if isinstance(expected, dict):
                if not expected or not set(expected) <= {"in", "not"}:
                    errs.append(f"{where}.when.{cpath}: only a value, {{in: [...]}} or {{not: value}} is allowed")
                elif "in" in expected and not isinstance(expected["in"], list):
                    errs.append(f"{where}.when.{cpath}: 'in' needs a list")
    return errs


def _validate_names(names: Any) -> list[str]:
    if names is None:
        return []
    if not isinstance(names, dict):
        return ["names: must be a mapping 'device-id: name'"]
    errs = []
    for dev_id, name in names.items():
        try:
            clean_name(name)
        except ValueError as exc:
            errs.append(f"names.{dev_id}: {exc}")
    return errs


def _validate_network(net: Any) -> list[str]:
    if net is None:
        return []
    if not isinstance(net, dict):
        return ["network: must be a mapping (subnet, dhcp_pool, blocks)"]
    errs = [f"network: unknown field '{k}' (allowed: {', '.join(sorted(_NETWORK_KEYS))})"
            for k in net if k not in _NETWORK_KEYS]
    subnets: list[ipaddress.IPv4Network] = []
    for key in ("subnet", "dhcp_pool"):
        try:
            nets = _networks(net.get(key))
        except ValueError as exc:
            errs.append(f"network.{key}: {exc}")
            continue
        if key == "subnet":
            subnets = nets
    blocks = net.get("blocks")
    if blocks is None:
        return errs
    if not isinstance(blocks, list):
        return errs + ["network.blocks: must be a list"]
    for i, blk in enumerate(blocks):
        where = f"network.blocks[{i}]"
        if not isinstance(blk, dict):
            errs.append(f"{where}: must be a mapping")
            continue
        errs.extend(f"{where}: unknown field '{k}' (allowed: {', '.join(sorted(_BLOCK_KEYS))})"
                    for k in blk if k not in _BLOCK_KEYS)
        match = blk.get("match")
        if not isinstance(match, dict) or not match:
            errs.append(f"{where}: 'match' is missing or empty")
        else:
            errs.extend(f"{where}.match: unknown field '{k}' (allowed: {', '.join(sorted(_MATCH_KEYS))})"
                        for k in match if k not in _MATCH_KEYS)
        if "range" not in blk:
            errs.append(f"{where}: 'range' is missing")
            continue
        try:
            start, end = parse_range(blk["range"])
        except ValueError as exc:
            errs.append(f"{where}.range: {exc}")
            continue
        if subnets and not any(start in n and end in n for n in subnets):
            errs.append(f"{where}.range: {blk['range']} is not fully inside network.subnet")
    return errs


# ---- Adressplan -----------------------------------------------------------------------------

def _networks(value: Any) -> list[ipaddress.IPv4Network]:
    """'192.168.200.0/24' or a list of them. Empty -> []."""
    if value in (None, "", []):
        return []
    items = value if isinstance(value, list) else [value]
    nets = []
    for item in items:
        try:
            net = ipaddress.ip_network(str(item).strip(), strict=False)
        except ValueError:
            raise ValueError(f"'{item}' is not a network in CIDR notation (example: 192.168.200.0/24)") from None
        if net.version != 4:
            raise ValueError(f"{net} is not an IPv4 network")
        nets.append(net)
    return nets


def parse_range(value: Any) -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]:
    """Address range as (first, last). Allowed:
    192.168.200.10-19 (last octet), 192.168.200.10-192.168.200.19, 192.168.200.16/28, 192.168.200.5."""
    text = str(value).strip()
    try:
        if "/" in text:
            net = ipaddress.ip_network(text, strict=False)
            if net.version != 4:
                raise ValueError
            hosts = list(net.hosts()) if net.num_addresses <= 65536 else []
            if not hosts:
                raise ValueError
            return hosts[0], hosts[-1]
        if "-" in text:
            a, b = (x.strip() for x in text.split("-", 1))
            if "." not in b:
                b = ".".join(a.split(".")[:3] + [b])
            start, end = ipaddress.ip_address(a), ipaddress.ip_address(b)
        else:
            start = end = ipaddress.ip_address(text)
        if start.version != 4 or end.version != 4:
            raise ValueError
    except ValueError:
        raise ValueError(f"'{value}' is not a valid range (example: 192.168.200.10-19)") from None
    if end < start:
        raise ValueError(f"'{value}': end is before start")
    return start, end


def address_block(policy: dict[str, Any], device: dict[str, Any]) -> dict[str, Any] | None:
    """First block from network.blocks whose match applies to the device."""
    for blk in (policy.get("network") or {}).get("blocks") or []:
        if profile_matches(blk.get("match") or {}, device):
            return blk
    return None


def _block_label(blk: dict[str, Any]) -> str:
    return blk.get("name") or str(blk.get("range"))


def next_free(blk: dict[str, Any], used: set[str]) -> str | None:
    start, end = parse_range(blk["range"])
    ip = start
    while ip <= end:
        if str(ip) not in used:
            return str(ip)
        ip += 1
    return None


def network_findings(policy: dict[str, Any], device: dict[str, Any], used_ips: set[str] | None = None,
                     reserve: bool = False) -> list[dict[str, Any]]:
    """Checks a device's address against network: subnet, dhcp_pool and blocks.

    used_ips: addresses of all known devices, for suggesting the next free address.
    reserve: add the suggestion to used_ips so the next device gets a different address
    (overview of all devices). The detail view of a single device does not reserve.
    """
    net = policy.get("network") or {}
    if not net:
        return []
    host = str(device.get("host") or "")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return []  # host name from EXTRA_HOSTS, no address plan can be checked
    subnets = _networks(net.get("subnet"))
    pools = _networks(net.get("dhcp_pool"))
    blocks = net.get("blocks") or []
    blk = address_block(policy, device)
    used = set(used_ips or ()) - {host}

    def finding(tag: str, note: str, expected: Any) -> dict[str, Any]:
        if blk:
            free = next_free(blk, used)
            if free and reserve and used_ips is not None:
                used_ips.add(free)
            note += f" Next free address in block: {free}." if free else f" Block '{_block_label(blk)}' is full."
        return {"kind": "network", "source": "Address plan", "path": "ip", "tag": tag,
                "expected": expected, "actual": host, "note": note}

    expected = (f"{blk['range']} ({_block_label(blk)})" if blk
                else ", ".join(str(n) for n in subnets) or "address block")
    out: list[dict[str, Any]] = []
    in_block = False
    if blk:
        start, end = parse_range(blk["range"])
        in_block = start <= ip <= end
    if any(ip in p for p in pools):
        out.append(finding("pool", "Address from the DHCP pool: new device without a fixed address yet.", expected))
    elif subnets and not any(ip in n for n in subnets):
        out.append(finding("outside", "Address is outside the Shelly network.", expected))
    elif blk and not in_block:
        out.append(finding("block", f"Address is not in block '{_block_label(blk)}'.", expected))
    if blocks and not blk:
        out.append({"kind": "network", "source": "Address plan", "path": "ip.block", "tag": "noblock",
                    "expected": "matching entry in network.blocks", "actual": f"{device.get('app')} · {device.get('id')}",
                    "note": "No address block matches this device. Check match or add a block."})
    return out


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "firmware": data.get("firmware") or {},
        "security": data.get("security") or {},
        "cover": data.get("cover") or {},
        "baseline": data.get("baseline") or {},
        "profiles": data.get("profiles") or [],
        "overrides": data.get("overrides") or {},
        "peer_ignore": data.get("peer_ignore") or [],
        "peer_depends": (copy.deepcopy(DEFAULT_PEER_DEPENDS) if data.get("peer_depends") is None
                         else data["peer_depends"]),
        "network": data.get("network") or {},
        "names": {str(k): clean_name(v) for k, v in (data.get("names") or {}).items()},
    }


def ensure_file(path: str) -> None:
    if not os.path.exists(path):
        with open(DEFAULT_POLICY_PATH, encoding="utf-8") as src:
            text = src.read()
        write_text(path, text)


def read_text(path: str) -> str:
    ensure_file(path)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write_text(path: str, text: str) -> None:
    """Write atomically, so a crash never leaves half a policy.yaml."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".policy.", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load(path: str) -> dict[str, Any]:
    return parse(read_text(path))


# ---- Auswerten ------------------------------------------------------------------------------

def profile_matches(match: dict[str, Any], device: dict[str, Any]) -> bool:
    for key, want in match.items():
        have = device.get(key)
        if have is None:
            have = ""
        wants = want if isinstance(want, list) else [want]
        if not any(fnmatchcase(str(have), str(w)) for w in wants):
            return False
    return True


def profile_applies(prof: dict[str, Any], device: dict[str, Any]) -> bool:
    """Profile applies to a device: assigned (`devices`) or `match` applies, unless it is in `exclude`."""
    dev_id = device.get("id")
    if dev_id in (prof.get("exclude") or []):
        return False
    if dev_id in (prof.get("devices") or []):
        return True
    match = prof.get("match") or {}
    return bool(match) and profile_matches(match, device)


def assignment(prof: dict[str, Any], device: dict[str, Any]) -> str | None:
    """'assigned', 'match', 'excluded' or None (for display)."""
    dev_id = device.get("id")
    if dev_id in (prof.get("exclude") or []):
        return "excluded"
    if dev_id in (prof.get("devices") or []):
        return "assigned"
    match = prof.get("match") or {}
    return "match" if match and profile_matches(match, device) else None


def matching_profiles(policy: dict[str, Any], device: dict[str, Any]) -> list[str]:
    return [p["name"] for p in policy["profiles"] if profile_applies(p, device)]


def device_ignore(policy: dict[str, Any], device: dict[str, Any]) -> list[str]:
    """ignore patterns from matching profiles and override. Also apply in the peer comparison: there this
    device's value does not count (e.g. cover:*.swap_inputs for push-buttons connected the other way round)."""
    return [pat for _, _, ign in layers(policy, device) for pat in ign]


def layers(policy: dict[str, Any], device: dict[str, Any]) -> list[tuple[str, dict[str, Any], list[str]]]:
    """[(source, settings, ignore)] in order of application: baseline, matching profiles, device override."""
    out: list[tuple[str, dict[str, Any], list[str]]] = [("Baseline", policy["baseline"] or {}, [])]
    for prof in policy["profiles"]:
        if profile_applies(prof, device):
            out.append((f"Profile: {prof['name']}", prof.get("settings") or {}, prof.get("ignore") or []))
    ov = (policy["overrides"] or {}).get(device["id"])
    if ov:
        out.append(("Override", ov.get("settings") or {}, ov.get("ignore") or []))
    return out


def effective_rules(policy: dict[str, Any], device: dict[str, Any]) -> dict[str, tuple[Any, str]]:
    """{pattern: (target, source)} for display. Later levels replace the same patterns,
    'ignore' removes patterns of the levels before."""
    rules: dict[str, tuple[Any, str]] = {}
    for source, settings, ignore in layers(policy, device):
        for pattern in list(rules):
            if pattern in ignore or matches_any(ignore, pattern):
                rules.pop(pattern)
        for pattern, val in settings.items():
            rules.pop(pattern, None)
            rules[pattern] = (val, source)
    return rules


def resolve(policy: dict[str, Any], device: dict[str, Any], flat: dict[str, Any]) -> tuple[
        dict[str, tuple[Any, str]], dict[str, tuple[Any, str]]]:
    """Resolves the levels against the actual paths of a device.

    Returns (concrete, missing): concrete = {path: (target, source)}, missing = {pattern: (target, source)}
    for rules whose component exists but whose setting does not. A later level
    overrides per concrete path, so 'cover:0.power_limit' replaces a baseline rule 'cover:*.power_limit'.
    """
    concrete: dict[str, tuple[Any, str]] = {}
    missing: dict[str, tuple[Any, str]] = {}
    for source, settings, ignore in layers(policy, device):
        if ignore:
            for path in list(concrete):
                if matches_any(ignore, path):
                    concrete.pop(path)
            for pattern in list(missing):
                if pattern in ignore or matches_any(ignore, pattern):
                    missing.pop(pattern)
        for pattern, expected in settings.items():
            if not component_exists(pattern, flat):
                continue  # the component does not exist on this device, e.g. cover on a Plus 1
            paths = expand_pattern(pattern, flat)
            if not paths:
                missing[pattern] = (expected, source)
                continue
            missing.pop(pattern, None)
            for path in paths:
                concrete[path] = (expected, source)
    return concrete, missing


def value_ok(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict) and expected and set(expected) <= _OPERATORS:
        if actual is MISSING:
            return False
        if "in" in expected and canon(actual) not in {canon(v) for v in expected["in"]}:
            return False
        if "not" in expected and canon(actual) == canon(expected["not"]):
            return False
        if "min" in expected and not (isinstance(actual, (int, float)) and actual >= expected["min"]):
            return False
        if "max" in expected and not (isinstance(actual, (int, float)) and actual <= expected["max"]):
            return False
        return True
    return canon(expected) == canon(actual)


def describe_expected(expected: Any) -> Any:
    if isinstance(expected, dict) and expected and set(expected) <= _OPERATORS:
        parts = []
        if "in" in expected:
            parts.append("one of " + ", ".join(canon(v) for v in expected["in"]))
        if "not" in expected:
            parts.append("not " + canon(expected["not"]))
        if "min" in expected:
            parts.append(f">= {expected['min']}")
        if "max" in expected:
            parts.append(f"<= {expected['max']}")
        return " and ".join(parts)
    return expected


def evaluate(
    policy: dict[str, Any],
    device: dict[str, Any],
    config: dict[str, Any] | None,
    status: dict[str, Any] | None,
    newest_ver_by_app: dict[str, str],
    used_ips: set[str] | None = None,
    reserve_addresses: bool = False,
) -> list[dict[str, Any]]:
    # The address plan needs no configuration: freshly found devices are checked too
    findings: list[dict[str, Any]] = network_findings(policy, device, used_ips, reserve_addresses)
    if config is None:
        return findings
    flat = flatten(config)
    concrete, missing = resolve(policy, device, flat)

    for pattern, (expected, source) in sorted(missing.items()):
        findings.append({
            "kind": "missing", "source": source, "path": pattern,
            "expected": describe_expected(expected), "actual": None,
            "note": "Setting does not exist on this device or firmware",
        })
    for path, (expected, source) in sorted(concrete.items()):
        actual = flat.get(path, MISSING)
        if not value_ok(expected, actual):
            fixed = not (isinstance(expected, dict) and expected and set(expected) <= _OPERATORS)
            findings.append({
                "kind": "mismatch", "source": source, "path": path,
                "expected": describe_expected(expected), "actual": None if actual is MISSING else actual,
                "fixable": fixed, "target": expected if fixed else None,   # fixed target value for "Fix"
            })

    fw = policy.get("firmware") or {}
    stable = None
    if fw.get("require_latest_stable"):
        upd = ((status or {}).get("sys") or {}).get("available_updates") or {}
        stable = (upd.get("stable") or {}).get("version")
        if stable:
            findings.append({
                "kind": "firmware", "source": "Firmware", "path": "firmware.stable",
                "expected": stable, "actual": device.get("ver"),
                "note": "Stable update available",
            })
    if fw.get("same_version_per_type"):
        newest = newest_ver_by_app.get(device.get("app") or "")
        # If the stable update already points to the same or a newer version, the one message is enough
        covered = stable is not None and newest is not None and version_key(stable) >= version_key(newest)
        if newest and not covered and version_key(device.get("ver")) < version_key(newest):
            findings.append({
                "kind": "firmware", "source": "Firmware", "path": "firmware.peer",
                "expected": newest, "actual": device.get("ver"),
                "note": "Older than other devices of the same type",
            })

    planned = (policy.get("names") or {}).get(device["id"])
    if planned is not None:
        actual = ((config.get("sys") or {}).get("device") or {}).get("name")
        if actual != planned:
            findings.append({
                "kind": "name", "source": "Naming plan", "path": "sys.device.name",
                "expected": planned, "actual": actual,
                "note": "Device has no name" if not actual else "Name differs from the naming plan",
            })

    # Shutters: only when calibrated is there a position and a measured power_thr for obstruction detection.
    # What counts is the status (pos_control), not maxtime_* from the configuration
    if (policy.get("cover") or {}).get("require_calibration"):
        for comp, st in sorted((status or {}).items()):
            if comp.startswith("cover:") and isinstance(st, dict) and st.get("pos_control") is False:
                findings.append({
                    "kind": "calibration", "source": "Calibration", "path": f"{comp}.pos_control",
                    "expected": "calibrated", "actual": "not calibrated",
                    "note": "Cover is not calibrated: no position and no measured obstruction threshold. "
                            "Run the calibration in the device web UI.",
                })

    sec = policy.get("security") or {}
    if sec.get("require_auth") and not device.get("auth_en"):
        findings.append({
            "kind": "security", "source": "Security", "path": "auth",
            "expected": "password protection on", "actual": "no password",
        })
    return findings
