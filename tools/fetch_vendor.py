"""Fetches Tabler from the official npm package and puts the CSS and license text into app/static/vendor/.

Runs in the Docker build (own stage) and locally for development and the demo. Version and SHA-256
of the tarball are pinned: a manipulated or different package fails the build.

Usage: python tools/fetch_vendor.py [target directory]   (default: app/static/vendor)
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tarfile
import urllib.request

TABLER_VERSION = "1.7.1"
TABLER_URL = f"https://registry.npmjs.org/@tabler/core/-/core-{TABLER_VERSION}.tgz"
TABLER_SHA256 = "1dad9578dddc5c50bcbe055c8cfa93c92a0f1fc52a37fe673965aec993005e98"

# File in the tarball -> name in the target directory
FILES = {
    "package/dist/css/tabler.min.css": "tabler.min.css",
}


# The npm package contains no LICENSE file, only the header comment in the CSS. So the full text lives here
# and is placed next to the CSS (MIT requires the copyright and license notice in all copies).
# Quelle: https://github.com/tabler/tabler/blob/main/LICENSE
TABLER_COPYRIGHT = "Copyright (c) 2018-2026 The Tabler Authors, Copyright (c) 2018-2026 codecalm.net Paweł Kuna"
TABLER_LICENSE = """The MIT License (MIT)

Copyright (c) 2018-2026 The Tabler Authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""


class IntegrityError(RuntimeError):
    pass


def verify(blob: bytes, expected: str) -> None:
    actual = hashlib.sha256(blob).hexdigest()
    if actual != expected:
        raise IntegrityError(f"SHA-256 mismatch: expected {expected}, got {actual}")


def extract(blob: bytes, target: str) -> list[str]:
    os.makedirs(target, exist_ok=True)
    written = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member, name in FILES.items():
            try:
                fh = tar.extractfile(member)
            except KeyError:
                fh = None
            if fh is None:
                raise IntegrityError(f"{member} missing from the package")
            path = os.path.join(target, name)
            with open(path, "wb") as out:
                out.write(fh.read())
            written.append(path)
    lic = os.path.join(target, "LICENSE-tabler.txt")
    with open(lic, "w", encoding="utf-8") as fh:
        fh.write(f"Tabler {TABLER_VERSION} (https://tabler.io), used for tabler.min.css\n\n{TABLER_LICENSE}")
    written.append(lic)
    with open(os.path.join(target, "VENDOR.json"), "w", encoding="utf-8") as fh:
        json.dump({"tabler": {"version": TABLER_VERSION, "source": TABLER_URL, "sha256": TABLER_SHA256,
                              "license": "MIT", "copyright": TABLER_COPYRIGHT,
                              "homepage": "https://tabler.io"}}, fh, indent=2, ensure_ascii=False)
    return written


def main(target: str) -> int:
    with urllib.request.urlopen(TABLER_URL, timeout=60) as resp:
        blob = resp.read()
    verify(blob, TABLER_SHA256)
    for path in extract(blob, target):
        print(f"ok  {path}")
    return 0


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(here, "..", "app", "static", "vendor")
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else default))
