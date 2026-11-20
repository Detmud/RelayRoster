"""Change profiles and overrides in policy.yaml from the UI.

The file stays the single source. ruamel.yaml reads and writes it round trip, so comments, order and
formatting are kept. The result is checked with the same validation as the YAML editor (policy.parse)
and only saved afterwards, with a backup of the old file.

Comments: ruamel attaches comment lines to the element *before* them (to its last leaf value). So that a
comment stays with the right entry when reordering, deleting or changing, `_rebuild` detaches the comments
per entry (end-of-line comment and preceding lines), rebuilds the entries and attaches the
comments again.
"""
from __future__ import annotations

import copy
import glob
import io
import os
import time
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import CommentMark
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
from ruamel.yaml.tokens import CommentToken

from . import policy as pol

BACKUPS_KEEP = 20
OPS = ("=", "in", "not", "min", "max")
_KEEP = object()


class EditError(ValueError):
    def __init__(self, errors: list[str], status: int = 422):
        super().__init__("; ".join(errors))
        self.errors = errors
        self.status = status


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_doc(text: str) -> Any:
    doc = _yaml().load(text)
    return CommentedMap() if doc is None else doc


def dump_doc(doc: Any) -> str:
    buf = io.StringIO()
    _yaml().dump(doc, buf)
    return buf.getvalue()


# ---- Comments per entry ---------------------------------------------------------------------

def _is_block(v: Any) -> bool:
    return isinstance(v, (CommentedMap, CommentedSeq)) and len(v) > 0 and not v.fa.flow_style()


def _keys(c: Any) -> list[Any]:
    return list(c.keys()) if isinstance(c, CommentedMap) else list(range(len(c)))


def _slot(c: Any, k: Any) -> tuple[Any, Any]:
    """Place where ruamel stores the comments *after* entry k: the last leaf below it."""
    v = c[k]
    if _is_block(v):
        return _slot(v, _keys(v)[-1])
    return c, k


def _tok_index(c: Any) -> int:
    return 2 if isinstance(c, CommentedMap) else 0


def _get_text(c: Any, k: Any) -> str:
    entry = c.ca.items.get(k)
    i = _tok_index(c)
    if not entry or len(entry) <= i or entry[i] is None:
        return ""
    return entry[i].value


def _set_text(c: Any, k: Any, text: str, col: int | None = None) -> None:
    i = _tok_index(c)
    if not text:
        entry = c.ca.items.get(k)
        if entry and len(entry) > i:
            entry[i] = None
            if not any(entry):
                c.ca.items.pop(k, None)
        return
    entry = c.ca.items.setdefault(k, [None, None, None, None])
    while len(entry) <= i:
        entry.append(None)
    if col is None:
        col = entry[i].column if entry[i] is not None else 0
    entry[i] = CommentToken(text, CommentMark(col), None)


def _get_col(c: Any, k: Any) -> int | None:
    entry = c.ca.items.get(k)
    i = _tok_index(c)
    if entry and len(entry) > i and entry[i] is not None and entry[i].value.startswith("#"):
        return entry[i].column
    return None


def _split(text: str) -> tuple[str, str]:
    """Comment text at one place -> (end-of-line comment, following lines)."""
    if not text:
        return "", ""
    if text.startswith("#"):
        nl = text.find("\n")
        return (text, "") if nl < 0 else (text[:nl], text[nl + 1:])
    return "", text.removeprefix("\n")


def _join(eol: str, rest: str) -> str:
    if not eol and not rest:
        return ""
    return f"{eol}\n{rest}" if eol else f"\n{rest}"


def _split_col(rest: str, col: int | None) -> tuple[str, str]:
    """Following lines after the last entry: deeper indented ones still belong to the entry, the rest to the next."""
    if col is None or not rest:
        return "", rest
    lines = rest.splitlines(keepends=True)
    cut = len(lines)
    for i, line in enumerate(lines):
        if line.strip() and len(line) - len(line.lstrip(" ")) < col:
            cut = i
            break
    while cut > 0 and not lines[cut - 1].strip():
        cut -= 1   # blank lines before the next entry belong to it
    return "".join(lines[:cut]), "".join(lines[cut:])


def _get_first(parent: tuple[Any, Any] | None) -> str:
    """Comment lines between 'profiles:' and the first entry (stored at the key in the parent object)."""
    if parent is None:
        return ""
    entry = parent[0].ca.items.get(parent[1])
    toks = entry[3] if entry and len(entry) > 3 and entry[3] else []
    return "".join((" " * t.column if t.value.strip() else "") + t.value for t in toks)


def _set_first(parent: tuple[Any, Any] | None, c: Any, text: str) -> None:
    if parent is None:
        return
    toks = []
    for line in text.splitlines(keepends=True):
        body = line.lstrip(" ")
        toks.append(CommentToken(body if body.strip() else "\n", CommentMark(len(line) - len(body) if body.strip() else 0),
                                 None))
    entry = parent[0].ca.items.setdefault(parent[1], [None, None, None, None])
    while len(entry) < 4:
        entry.append(None)
    entry[3] = toks or None
    if c.ca.comment and len(c.ca.comment) > 1:
        c.ca.comment[1] = toks
    if not any(entry):
        parent[0].ca.items.pop(parent[1], None)


def _rebuild(c: Any, entries: list[tuple[Any, ...]], item_col: int | None = None, before=None,
             parent: tuple[Any, Any] | None = None) -> None:
    """Rebuilds map or list c from entries.

    Map: [(old key or None, new key, value, eol)], list: [(old index or None, value, eol)].
    eol: comment text without '#', "" = none, _KEEP = keep the existing one (None for list entries).
    Comment lines before an entry move with it; if an entry is dropped, its preceding lines go to
    the next one. item_col: column of the list entries, see _split_col. before: called after detaching the
    comments and before rebuilding (change the entry itself without comments shifting).
    parent: (parent object, key) of a list, where the lines before its first entry are stored.
    Lists: the lines before a removed entry (its description) are dropped with it, new entries get
    a blank line before them. Maps: the lines move to the next entry (often commented-out rules).
    """
    is_map = isinstance(c, CommentedMap)
    old = _keys(c)
    eols: dict[Any, str] = {}
    leads: dict[Any, str] = {}
    own: dict[Any, str] = {}      # lines after a list entry that still belong to it
    keytok: dict[Any, Any] = {}   # own comment of a key with a block value
    cols: dict[Any, int] = {}
    tail = ""
    for i, k in enumerate(old):
        if _is_block(c[k]):
            if c.ca.items.get(k):
                keytok[k] = list(c.ca.items[k])
            sc, sk = _slot(c, k)
            s_eol, rest = _split(_get_text(sc, sk))
            _set_text(sc, sk, _join(s_eol, ""))
            eol = ""
        else:
            col = _get_col(c, k)
            if col is not None:
                cols[k] = col
            eol, rest = _split(_get_text(c, k))
        inner, outer = ("", rest) if is_map else _split_col(rest, item_col)
        own[k] = inner
        eols[k] = eol
        if i + 1 < len(old):
            leads[old[i + 1]] = outer
        else:
            tail = outer
    if old:
        leads[old[0]] = _get_first(parent) if not is_map else ""
    # Column for new end-of-line comments: like the existing ones in this map
    default_col = max(set(cols.values()), key=list(cols.values()).count) if cols else None
    if before is not None:
        before()

    kept = {e[0] for e in entries if e[0] is not None}
    carry = ""
    for k in old:
        lead = carry + leads.get(k, "")
        if k in kept:
            leads[k], carry = lead, ""
        elif is_map:
            carry = lead
    tail = carry + tail

    items = []
    for e in entries:
        ok, nk, val, eol = e if is_map else (e[0], None, e[1], e[2])
        if eol is _KEEP:
            eol_text = eols.get(ok, "")
        else:
            eol_text = f"# {eol}" if eol else ""
        items.append({"old": ok, "key": nk, "val": val, "eol": eol_text, "col": cols.get(ok, default_col),
                      "lead": leads.get(ok, "") if ok is not None else "",
                      "own": own.get(ok, "") if ok is not None else ""})

    if is_map:
        for k in old:
            del c[k]
        c.ca.items.clear()
        for it in items:
            c[it["key"]] = it["val"]
    else:
        del c[:]
        c.ca.items.clear()
        for it in items:
            c.append(it["val"])

    if not is_map:
        # A blank line between list entries if the file does so; none before the first
        sep = any(leads.get(k, "").startswith("\n") for k in old[1:]) or len(old) < 2
        for idx, it in enumerate(items):
            lead = it["lead"].lstrip("\n") if idx == 0 else it["lead"]
            if idx > 0 and sep and not lead.startswith("\n"):
                lead = "\n" + lead
            it["lead"] = lead
        if parent is not None:
            _set_first(parent, c, items[0]["lead"] if items else "")
    for idx, k in enumerate(_keys(c)):
        it = items[idx]
        rest = it["own"] + (items[idx + 1]["lead"] if idx + 1 < len(items) else tail)
        if _is_block(it["val"]):
            if it["old"] in keytok:
                c.ca.items[k] = keytok[it["old"]]
            sc, sk = _slot(c, k)
            s_eol, _ = _split(_get_text(sc, sk))
            _set_text(sc, sk, _join(s_eol, rest))
        else:
            _set_text(c, k, _join(it["eol"], rest), it["col"] if it["eol"] else 0)


# ---- Profil <-> JSON ------------------------------------------------------------------------

def _plain(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, str):
        return str(v)
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float):
        return float(v)
    return v


def rule_from_value(path: str, value: Any, comment: str = "") -> dict[str, Any]:
    """Value from the policy -> {path, op, value, comment}. Several operators: op 'expr', value = mapping."""
    v = _plain(value)
    if isinstance(v, dict) and v and set(v) <= set(OPS[1:]):
        if len(v) == 1:
            op = next(iter(v))
            return {"path": path, "op": op, "value": v[op], "comment": comment}
        return {"path": path, "op": "expr", "value": v, "comment": comment}
    return {"path": path, "op": "=", "value": v, "comment": comment}


def value_from_rule(rule: dict[str, Any]) -> Any:
    op, val = rule.get("op", "="), rule.get("value")
    return val if op in ("=", "expr") else {op: val}


def _comment_of(c: Any, k: Any) -> str:
    if _is_block(c[k]):
        return ""
    eol, _ = _split(_get_text(*_slot(c, k)))
    return eol.lstrip("#").strip()


def profile_json(prof: Any) -> dict[str, Any]:
    settings = prof.get("settings") or {}
    rules = []
    for path in settings:
        comment = _comment_of(settings, path) if isinstance(settings, CommentedMap) else ""
        rules.append(rule_from_value(str(path), settings[path], comment))
    return {
        "name": str(prof.get("name") or ""),
        "match": _plain(prof.get("match") or {}),
        "settings": rules,
        "ignore": [str(i) for i in (prof.get("ignore") or [])],
        "devices": [str(i) for i in (prof.get("devices") or [])],
        "exclude": [str(i) for i in (prof.get("exclude") or [])],
    }


def _id_list(v: Any) -> tuple[list[str], bool]:
    """Clean a list of device IDs. Returns (list, ok)."""
    if v in (None, ""):
        return [], True
    if not isinstance(v, list):
        return [], False
    return list(dict.fromkeys(str(x).strip() for x in v if str(x).strip())), True


def _check_profile(p: Any) -> tuple[dict[str, Any], list[str]]:
    errs: list[str] = []
    if not isinstance(p, dict):
        return {}, ["Profile must be an object"]
    name = str(p.get("name") or "").strip()
    if not name:
        errs.append("Name is missing")
    match = p.get("match") or {}
    if not isinstance(match, dict):
        errs.append("match must be an object")
        match = {}
    clean_match: dict[str, Any] = {}
    for k, v in match.items():
        if v in (None, "", []):
            continue
        if isinstance(v, list):
            v = [str(x).strip() for x in v if str(x).strip()]
            if not v:
                continue
            clean_match[k] = v if len(v) > 1 else v[0]
        else:
            clean_match[k] = str(v).strip()
    lists: dict[str, list[str]] = {}
    for key in ("devices", "exclude"):
        lists[key], ok = _id_list(p.get(key))
        if not ok:
            errs.append(f"{key} must be a list of device IDs")
    if not clean_match and not lists["devices"]:
        errs.append("Match needs at least one field, or assign devices")
    rules = p.get("settings") or []
    if not isinstance(rules, list):
        errs.append("settings must be a list")
        rules = []
    seen: set[str] = set()
    clean_rules = []
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            errs.append(f"Rule {i + 1}: invalid")
            continue
        path = str(r.get("path") or "").strip()
        if not path:
            errs.append(f"Rule {i + 1}: path is missing")
            continue
        if path in seen:
            errs.append(f"Rule {i + 1}: {path} appears twice")
        seen.add(path)
        op = r.get("op") or "="
        if op not in OPS and op != "expr":
            errs.append(f"{path}: unknown operator {op!r}")
            continue
        val = r.get("value")
        if op == "in" and not isinstance(val, list):
            errs.append(f"{path}: 'in' needs a list")
        if op in ("min", "max") and (isinstance(val, bool) or not isinstance(val, (int, float))):
            errs.append(f"{path}: '{op}' needs a number")
        if op == "expr" and not (isinstance(val, dict) and val and set(val) <= set(OPS[1:])):
            errs.append(f"{path}: invalid operator expression")
        comment = str(r.get("comment") or "").replace("\n", " ").strip()
        clean_rules.append({"path": path, "op": op, "value": val, "comment": comment})
    ignore = p.get("ignore") or []
    if not isinstance(ignore, list):
        errs.append("ignore must be a list")
        ignore = []
    ignore = [str(i).strip() for i in ignore if str(i).strip()]
    return {"name": name, "match": clean_match, "settings": clean_rules, "ignore": ignore,
            "devices": lists["devices"], "exclude": lists["exclude"]}, errs


def check_rule(rule: Any) -> tuple[dict[str, Any], list[str]]:
    """Check one rule {path, op, value, comment} (override from the device page)."""
    if not isinstance(rule, dict):
        return {}, ["Rule must be an object"]
    p, errs = _check_profile({"name": "x", "match": {"id": "x"}, "settings": [rule]})
    return (p["settings"][0] if p.get("settings") else {}), errs


def _scalar(v: Any) -> Any:
    """Quote text that PyYAML (YAML 1.1, as policy.parse checks) would read differently (on, yes, 1e3 …)."""
    if isinstance(v, str):
        try:
            same = yaml.safe_load(v) == v
        except yaml.YAMLError:
            same = False
        if not same or v != v.strip() or not v:
            return DoubleQuotedScalarString(v)
    return v


def _node(v: Any, flow: bool = True) -> Any:
    """JSON value -> ruamel node. Operators and lists as flow ({max: 1}, [a, b])."""
    if isinstance(v, dict):
        m = CommentedMap()
        for k, x in v.items():
            m[_scalar(k)] = _node(x, flow)
        if flow:
            m.fa.set_flow_style()
        return m
    if isinstance(v, list):
        s = CommentedSeq([_node(x, flow) for x in v])
        if flow:
            s.fa.set_flow_style()
        return s
    return _scalar(v)


def _same(a: Any, b: Any) -> bool:
    return _plain(a) == _plain(b)


def _apply_settings(target: CommentedMap, rules: list[dict[str, Any]]) -> None:
    entries = []
    for r in rules:
        new_val = value_from_rule(r)
        if r["path"] in target:
            old = target[r["path"]]
            val = old if _same(old, new_val) else _node(new_val)
            eol = r["comment"] if r["comment"] != _comment_of(target, r["path"]) else _KEEP
            entries.append((r["path"], r["path"], val, eol))
        else:
            entries.append((None, _scalar(r["path"]), _node(new_val), r["comment"]))
    _rebuild(target, entries)


def _apply_match(target: CommentedMap, match: dict[str, Any]) -> None:
    entries = []
    for k, v in match.items():
        if k in target:
            entries.append((k, k, target[k] if _same(target[k], v) else _node(v), _KEEP))
        else:
            entries.append((None, k, _node(v), ""))
    _rebuild(target, entries)


def _fill_profile(target: CommentedMap, p: dict[str, Any]) -> None:
    """Writes name, match, settings, ignore into the existing map (other keys stay)."""
    if target.get("name") != p["name"]:
        target["name"] = p["name"]
    if p["match"]:
        match = target.get("match")
        if not isinstance(match, CommentedMap):
            match = CommentedMap()
            target["match"] = match
        _apply_match(match, p["match"])
    elif "match" in target:
        _rebuild(target, [(k, k, target[k], _KEEP) for k in target if k != "match"])
    settings = target.get("settings")
    if p["settings"]:
        if not isinstance(settings, CommentedMap):
            settings = CommentedMap()
            target["settings"] = settings
        if settings.fa.flow_style():
            settings.fa.set_block_style()
        _apply_settings(settings, p["settings"])
    elif "settings" in target:
        _rebuild(target, [(k, k, target[k], _KEEP) for k in target if k != "settings"])
    if p["ignore"]:
        if not _same(target.get("ignore"), p["ignore"]):
            target["ignore"] = _node(p["ignore"])
    elif "ignore" in target:
        _rebuild(target, [(k, k, target[k], _KEEP) for k in target if k != "ignore"])
    for key in ("devices", "exclude"):     # Zuweisung
        ids = p.get(key) or []
        if ids:
            if key in target:
                if not _same(target[key], ids):
                    target[key] = _node(ids)
            else:
                # insert right after match/name: comments at the end of settings stay there
                keys = list(target)
                anchor = "devices" if key == "exclude" and "devices" in target else (
                    "match" if "match" in target else "name")
                pos = keys.index(anchor) + 1 if anchor in keys else len(keys)
                target.insert(pos, key, _node(ids))
        elif key in target:
            _rebuild(target, [(k, k, target[k], _KEEP) for k in target if k != key])


def _new_profile(p: dict[str, Any]) -> CommentedMap:
    m = CommentedMap()
    m["name"] = p["name"]
    if p["match"]:
        m["match"] = CommentedMap()
    m["settings"] = CommentedMap()
    _fill_profile(m, p)
    if not p["settings"]:
        m.pop("settings", None)
    return m


# ---- Operations on the document -------------------------------------------------------------

def _profiles(doc: Any, create: bool = False) -> CommentedSeq | None:
    seq = doc.get("profiles")
    if isinstance(seq, CommentedSeq):
        if create and seq.fa.flow_style():
            seq.fa.set_block_style()
        return seq
    if not create:
        return None
    seq = CommentedSeq()
    doc["profiles"] = seq
    return seq


def _item_col(seq: CommentedSeq) -> int | None:
    for item in seq:
        lc = getattr(item, "lc", None)
        if lc is not None and lc.col is not None and lc.line is not None:
            return lc.col
    return None


def _find(seq: CommentedSeq | None, name: str) -> int:
    if seq is None:
        return -1
    for i, prof in enumerate(seq):
        if isinstance(prof, dict) and str(prof.get("name")) == name:
            return i
    return -1


def list_profiles(doc: Any) -> list[dict[str, Any]]:
    return [profile_json(p) for p in (_profiles(doc) or []) if isinstance(p, dict)]


def upsert_profile(doc: Any, profile: Any, original: str | None = None, position: int | None = None) -> str:
    """Creates a profile (original None) or changes it. Returns the name."""
    p, errs = _check_profile(profile)
    if errs:
        raise EditError(errs)
    seq = _profiles(doc, create=True)
    idx = _find(seq, original) if original is not None else -1
    if original is not None and idx < 0:
        raise EditError([f"Profile '{original}' does not exist"], 404)
    other = _find(seq, p["name"])
    if other >= 0 and other != idx:
        raise EditError([f"A profile named '{p['name']}' already exists"], 409)
    col = _item_col(seq)
    if idx >= 0:
        target = seq[idx]
        _rebuild(seq, [(i, seq[i], None) for i in range(len(seq))], col, before=lambda: _fill_profile(target, p),
                 parent=(doc, "profiles"))
        return p["name"]
    entries = [(i, seq[i], None) for i in range(len(seq))]
    new = (None, _new_profile(p), None)
    pos = len(entries) if position is None else max(0, min(int(position), len(entries)))
    entries.insert(pos, new)
    _rebuild(seq, entries, col, parent=(doc, "profiles"))
    return p["name"]


def delete_profile(doc: Any, name: str) -> None:
    seq = _profiles(doc)
    idx = _find(seq, name)
    if idx < 0:
        raise EditError([f"Profile '{name}' does not exist"], 404)
    _rebuild(seq, [(i, seq[i], None) for i in range(len(seq)) if i != idx], _item_col(seq), parent=(doc, "profiles"))


def reorder_profiles(doc: Any, names: list[str]) -> None:
    seq = _profiles(doc)
    current = [str(p.get("name")) if isinstance(p, dict) else None for p in (seq or [])]
    if sorted(n for n in current if n is not None) != sorted(names) or len(set(names)) != len(names):
        raise EditError(["The order must list every profile exactly once"])
    order = [current.index(n) for n in names]
    # entries without a name (invalid) stay at the end
    order += [i for i, n in enumerate(current) if n is None]
    _rebuild(seq, [(i, seq[i], None) for i in order], _item_col(seq), parent=(doc, "profiles"))


def set_override(doc: Any, device_id: str, path: str, value: Any, comment: str) -> None:
    """Set a rule in overrides.<id>.settings (new or replace), comment required."""
    path = str(path or "").strip()
    comment = str(comment or "").replace("\n", " ").strip()
    errs = []
    if not path:
        errs.append("Path is missing")
    if not comment:
        errs.append("A comment is required: why and when (e.g. running power 140 W measured 2026-11-02)")
    if errs:
        raise EditError(errs)
    new_val = _node(_plain(value))
    overrides = doc.get("overrides")
    if not isinstance(overrides, CommentedMap):
        overrides = CommentedMap()
        doc["overrides"] = overrides
    if overrides.fa.flow_style():
        overrides.fa.set_block_style()
    ov = overrides.get(device_id)
    if not isinstance(ov, CommentedMap) or not ov:
        settings = CommentedMap()
        settings[_scalar(path)] = new_val
        _set_text(settings, path, f"# {comment}")
        ov = CommentedMap()
        ov["settings"] = settings
        entries = [(k, k, overrides[k], _KEEP) for k in overrides if k != device_id]
        entries.append((device_id if device_id in overrides else None, device_id, ov, ""))
        _rebuild(overrides, entries)
        return
    if ov.fa.flow_style():
        ov.fa.set_block_style()
    settings = ov.get("settings")
    if not isinstance(settings, CommentedMap) or not settings:
        settings = CommentedMap()
        settings[_scalar(path)] = new_val
        _set_text(settings, path, f"# {comment}")
        entries = [(k, k, ov[k], _KEEP) for k in ov if k != "settings"]
        entries.insert(0, ("settings" if "settings" in ov else None, "settings", settings, ""))
        _rebuild(ov, entries)
        return
    if settings.fa.flow_style():
        settings.fa.set_block_style()
    entries = [(k, k, new_val if k == path else settings[k], comment if k == path else _KEEP) for k in settings]
    if path not in settings:
        entries.append((None, _scalar(path), new_val, comment))
    _rebuild(settings, entries)


def assign_devices(doc: Any, name: str, add: list[str], remove: list[str], by_match: set[str]) -> None:
    """Assign devices to a profile or take them out.

    add: if the device is in `exclude`, it is removed there; if `match` does not apply, it goes to `devices`.
    remove: out of `devices`; if `match` still applies, it goes to `exclude`. by_match: IDs that `match`
    of the profile applies to (computed by the caller from the device data).
    """
    seq = _profiles(doc)
    idx = _find(seq, name)
    if idx < 0:
        raise EditError([f"Profile '{name}' does not exist"], 404)
    p = profile_json(seq[idx])
    devices, exclude = list(p["devices"]), list(p["exclude"])
    for dev_id in add:
        if dev_id in exclude:
            exclude.remove(dev_id)
        if dev_id not in by_match and dev_id not in devices:
            devices.append(dev_id)
    for dev_id in remove:
        if dev_id in devices:
            devices.remove(dev_id)
        if dev_id in by_match and dev_id not in exclude:
            exclude.append(dev_id)
    if not p["match"] and not devices:
        raise EditError([f"Profile '{name}' has no match rule, at least one device must stay assigned"])
    p["devices"], p["exclude"] = devices, exclude
    upsert_profile(doc, p, name)


def _rule_map(doc: Any, key: str, create: bool) -> CommentedMap | None:
    m = doc.get(key)
    if isinstance(m, CommentedMap):
        if m.fa.flow_style():
            m.fa.set_block_style()
        return m
    if not create:
        return None
    m = CommentedMap()
    doc[key] = m
    return m


def set_baseline(doc: Any, path: str, value: Any, comment: str) -> None:
    """Set a rule in baseline (new or replace), e.g. after a change for all devices."""
    path = str(path or "").strip()
    if not path:
        raise EditError(["Path is missing"])
    comment = str(comment or "").replace("\n", " ").strip()
    base = _rule_map(doc, "baseline", create=True)
    new_val = _node(_plain(value))
    if not base:
        base[_scalar(path)] = new_val
        if comment:
            _set_text(base, path, f"# {comment}")
        return
    entries = [(k, k, new_val if k == path else base[k], (comment or _KEEP) if k == path else _KEEP) for k in base]
    if path not in base:
        entries.append((None, _scalar(path), new_val, comment))
    _rebuild(base, entries)


def check_rules(rules: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Check a list of rules {path, op, value, comment} (baseline editor)."""
    p, errs = _check_profile({"name": "x", "match": {"id": "x"}, "settings": rules})
    return p.get("settings") or [], errs


def baseline_json(doc: Any) -> list[dict[str, Any]]:
    """Baseline rules as {path, op, value, comment}, in file order."""
    return profile_json({"settings": doc.get("baseline") if isinstance(doc, dict) else None})["settings"]


def list_overrides(doc: Any) -> list[dict[str, Any]]:
    """Device overrides as [{id, settings: [rules], ignore}], in file order."""
    ov = doc.get("overrides") if isinstance(doc, dict) else None
    out = []
    for dev_id, entry in (ov or {}).items():
        entry = entry if isinstance(entry, dict) else {}
        out.append({"id": str(dev_id), "settings": profile_json(entry)["settings"],
                    "ignore": [str(i) for i in (entry.get("ignore") or [])]})
    return out


def set_baseline_rules(doc: Any, rules: Any) -> None:
    """Replace all baseline rules. Comments and order of unchanged rules stay."""
    clean, errs = check_rules(rules)
    if errs:
        raise EditError(errs, 422)
    base = _rule_map(doc, "baseline", create=bool(clean))
    if base is None:
        return
    _apply_settings(base, clean)


def preview_baseline(policy_data: dict[str, Any], rules: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Policy (already checked, as dict) with the baseline replaced, without a file."""
    clean, errs = check_rules(rules)
    if errs:
        raise EditError(errs)
    data = copy.deepcopy(policy_data)
    data["baseline"] = {r["path"]: value_from_rule(r) for r in clean}
    return data, {"name": "Baseline", "settings": data["baseline"]}


def remove_rule(doc: Any, source: str, path: str, device_id: str | None = None) -> None:
    """Removes a rule (outdated rules). source as in the findings: Baseline, Profile: <name>, Override."""
    if source == "Baseline":
        target = _rule_map(doc, "baseline", create=False)
    elif source.startswith("Profile: "):
        seq = _profiles(doc)
        idx = _find(seq, source[len("Profile: "):])
        target = seq[idx].get("settings") if idx >= 0 else None
    elif source == "Override" and device_id:
        ov = (doc.get("overrides") or {}).get(device_id)
        target = ov.get("settings") if isinstance(ov, dict) else None
    else:
        raise EditError([f"Unknown rule source '{source}'"])
    if not isinstance(target, CommentedMap) or path not in target:
        raise EditError([f"{source}: rule {path} does not exist"], 404)
    _rebuild(target, [(k, k, target[k], _KEEP) for k in target if k != path])


# ---- Speichern ------------------------------------------------------------------------------

def backup(path: str) -> str | None:
    """Backup of the current file as policy.yaml.bak-<time>, the last BACKUPS_KEEP remain."""
    if not os.path.exists(path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = f"{path}.bak-{stamp}"
    n = 1
    while os.path.exists(target):
        n += 1
        target = f"{path}.bak-{stamp}-{n}"
    with open(path, encoding="utf-8") as src:
        text = src.read()
    pol.write_text(target, text)
    files = sorted(glob.glob(glob.escape(path) + ".bak-*"), key=os.path.getmtime)
    for old in files[:-BACKUPS_KEEP]:
        try:
            os.unlink(old)
        except OSError:
            pass
    return target


def save_text(path: str, text: str) -> None:
    """Checks like the YAML editor, backs up the old file and writes atomically."""
    pol.parse(text)   # PolicyError on errors, then nothing is written
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return
    except FileNotFoundError:
        pass
    backup(path)
    pol.write_text(path, text)


def candidate(text: str, change) -> tuple[str, dict[str, Any]]:
    """Applies change(doc) to a copy. Returns (new text, checked policy) or raises."""
    doc = load_doc(text)
    change(doc)
    new_text = dump_doc(doc)
    return new_text, pol.parse(new_text)


def preview_policy(policy_data: dict[str, Any], profile: Any, original: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Policy (already checked, as dict) with the profile replaced or appended, without a file."""
    p, errs = _check_profile(profile)
    if errs:
        raise EditError(errs)
    data = copy.deepcopy(policy_data)
    prof = {"name": p["name"], "match": p["match"],
            "settings": {r["path"]: value_from_rule(r) for r in p["settings"]}, "ignore": p["ignore"],
            "devices": p["devices"], "exclude": p["exclude"]}
    profiles = data["profiles"]
    idx = next((i for i, x in enumerate(profiles) if x.get("name") == original), -1) if original else -1
    if any(x.get("name") == p["name"] for i, x in enumerate(profiles) if i != idx):
        raise EditError([f"A profile named '{p['name']}' already exists"], 409)
    if idx >= 0:
        profiles[idx] = prof
    else:
        profiles.append(prof)
    return data, prof
