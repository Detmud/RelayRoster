"""Integrity check of tools/fetch_vendor.py, without network.

Usage: python tests/test_vendor.py   (or pytest)
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import fetch_vendor as fv  # noqa: E402


def make_tgz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_pinned_values():
    assert fv.TABLER_VERSION == "1.7.1"
    assert fv.TABLER_URL == "https://registry.npmjs.org/@tabler/core/-/core-1.7.1.tgz"
    assert len(fv.TABLER_SHA256) == 64 and int(fv.TABLER_SHA256, 16) >= 0


def test_wrong_hash_is_rejected():
    blob = make_tgz({"package/dist/css/tabler.min.css": b"body{}"})
    try:
        fv.verify(blob, "0" * 64)
        raise AssertionError("a wrong hash must be rejected")
    except fv.IntegrityError as exc:
        assert "SHA-256" in str(exc)
    fv.verify(blob, hashlib.sha256(blob).hexdigest())


def test_extract_writes_css_and_manifest():
    blob = make_tgz({"package/dist/css/tabler.min.css": b"/* tabler */body{}", "package/other.txt": b"x"})
    with tempfile.TemporaryDirectory() as tmp:
        fv.extract(blob, tmp)
        assert sorted(os.listdir(tmp)) == ["LICENSE-tabler.txt", "VENDOR.json", "tabler.min.css"]
        with open(os.path.join(tmp, "LICENSE-tabler.txt"), encoding="utf-8") as fh:
            lic = fh.read()
        assert "The Tabler Authors" in lic and "Permission is hereby granted" in lic
        with open(os.path.join(tmp, "tabler.min.css"), "rb") as fh:
            assert fh.read() == b"/* tabler */body{}"
        with open(os.path.join(tmp, "VENDOR.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
        assert meta["tabler"]["version"] == fv.TABLER_VERSION and meta["tabler"]["license"] == "MIT"
        assert "Paweł Kuna" in meta["tabler"]["copyright"]


def test_missing_file_is_rejected():
    blob = make_tgz({"package/README.md": b"x"})
    with tempfile.TemporaryDirectory() as tmp:
        try:
            fv.extract(blob, tmp)
            raise AssertionError("a missing CSS must be rejected")
        except fv.IntegrityError as exc:
            assert "missing from the package" in str(exc)


if __name__ == "__main__":
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
