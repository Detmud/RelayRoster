"""Device types for Shelly model identifiers, and MAC addresses in the usual format.

`Shelly.GetDeviceInfo` returns `model` (order number like `S4SW-002P16EU`) and `app` (firmware family
like `Plus1`). The UI puts the function first and shows the rest in a second line:

- kind: what the hardware does (`2PM`, `i4`, `Plug`), shown as `Shelly 2PM`
- generation: `Gen2` for Plus and Pro, `Gen3`, `Gen4`
- variant: the form factor, `Pro` (DIN rail) or `Mini`; `Standard` is not shown
- suffix: supply voltage or socket standard (`DC`, `S`, `IT`, `UK`, `US`), not a variant

Example: Shelly Pro 2PM -> `Shelly 2PM` / `Gen2 · Pro`. Group headings put both in one line
(`Shelly 2PM · Gen2 · Pro`), because devices of different generations or variants get different firmware
and settings. Unknown identifiers fall back to a name derived from `app`; the raw values stay visible
in the details.
"""
from __future__ import annotations

import re
from typing import Any

# kind, variant ("" = Standard, "Pro", "Mini"), suffix, generation
DeviceKind = tuple[str, str, str, int]

# Order number -> device type. Country/variant suffixes of the order number (EU, UK, US …) are listed
# individually, because Shelly does not form them consistently. The comment is the sales name.
MODELS: dict[str, DeviceKind] = {
    # Plus (Gen2)
    "SNSW-001X16EU": ("1", "", "", 2),                # Shelly Plus 1
    "SNSW-001X8EU": ("1", "Mini", "", 2),             # Shelly Plus 1 Mini
    "SNSW-001P16EU": ("1PM", "", "", 2),              # Shelly Plus 1PM
    "SNSW-001P8EU": ("1PM", "Mini", "", 2),           # Shelly Plus 1PM Mini
    "SNSW-002P16EU": ("2PM", "", "", 2),              # Shelly Plus 2PM
    "SNSW-102P16EU": ("2PM", "", "", 2),              # Shelly Plus 2PM
    "SNSN-0024X": ("i4", "", "", 2),                  # Shelly Plus i4
    "SNSN-0D24X": ("i4", "", "DC", 2),                # Shelly Plus i4 DC
    "SNPL-00112EU": ("Plug", "", "S", 2),             # Shelly Plus Plug S
    "SNPL-10112EU": ("Plug", "", "S", 2),             # Shelly Plus Plug S
    "SNPL-00110IT": ("Plug", "", "IT", 2),            # Shelly Plus Plug IT
    "SNPL-00112UK": ("Plug", "", "UK", 2),            # Shelly Plus Plug UK
    "SNPL-00116US": ("Plug", "", "US", 2),            # Shelly Plus Plug US
    "SNPM-001PCEU16": ("PM", "Mini", "", 2),          # Shelly Plus PM Mini
    "SNSN-0013A": ("H&T", "", "", 2),                 # Shelly Plus H&T
    "SNSN-0031Z": ("Smoke", "", "", 2),               # Shelly Plus Smoke
    "SNDM-0013US": ("Wall Dimmer", "", "", 2),        # Shelly Plus Wall Dimmer
    # Pro (Gen2)
    "SPSW-001XE16EU": ("1", "Pro", "", 2),            # Shelly Pro 1
    "SPSW-201XE16EU": ("1", "Pro", "", 2),            # Shelly Pro 1
    "SPSW-001PE16EU": ("1PM", "Pro", "", 2),          # Shelly Pro 1PM
    "SPSW-201PE16EU": ("1PM", "Pro", "", 2),          # Shelly Pro 1PM
    "SPSW-002XE16EU": ("2", "Pro", "", 2),            # Shelly Pro 2
    "SPSW-202XE16EU": ("2", "Pro", "", 2),            # Shelly Pro 2
    "SPSW-002PE16EU": ("2PM", "Pro", "", 2),          # Shelly Pro 2PM
    "SPSW-202PE16EU": ("2PM", "Pro", "", 2),          # Shelly Pro 2PM
    "SPSW-003XE16EU": ("3", "Pro", "", 2),            # Shelly Pro 3
    "SPSW-004PE16EU": ("4PM", "Pro", "", 2),          # Shelly Pro 4PM
    "SPSW-104PE16EU": ("4PM", "Pro", "", 2),          # Shelly Pro 4PM
    "SPEM-003CEBEU": ("3EM", "Pro", "", 2),           # Shelly Pro 3EM
    # Gen3
    "S3SW-001X16EU": ("1", "", "", 3),                # Shelly 1 Gen3
    "S3SW-001P16EU": ("1PM", "", "", 3),              # Shelly 1PM Gen3
    "S3SW-001X8EU": ("1", "Mini", "", 3),             # Shelly 1 Mini Gen3
    "S3SW-001P8EU": ("1PM", "Mini", "", 3),           # Shelly 1PM Mini Gen3
    "S3SW-002P16EU": ("2PM", "", "", 3),              # Shelly 2PM Gen3
    "S3PM-001PCEU16": ("PM", "Mini", "", 3),          # Shelly PM Mini Gen3
    "S3SN-0024X": ("i4", "", "", 3),                  # Shelly i4 Gen3
    "S3SN-0U12A": ("H&T", "", "", 3),                 # Shelly H&T Gen3
    # Gen4
    "S4SW-001X16EU": ("1", "", "", 4),                # Shelly 1 Gen4
    "S4SW-001P16EU": ("1PM", "", "", 4),              # Shelly 1PM Gen4
    "S4SW-001X8EU": ("1", "Mini", "", 4),             # Shelly 1 Mini Gen4
    "S4SW-001P8EU": ("1PM", "Mini", "", 4),           # Shelly 1PM Mini Gen4
    "S4SW-002P16EU": ("2PM", "", "", 4),              # Shelly 2PM Gen4
}

# Firmware family (`app`) -> device type, if the order number is unknown
APPS: dict[str, DeviceKind] = {
    "Plus1": ("1", "", "", 2),                        # Shelly Plus 1
    "Plus1Mini": ("1", "Mini", "", 2),                # Shelly Plus 1 Mini
    "Plus1PM": ("1PM", "", "", 2),                    # Shelly Plus 1PM
    "Plus1PMMini": ("1PM", "Mini", "", 2),            # Shelly Plus 1PM Mini
    "Plus2PM": ("2PM", "", "", 2),                    # Shelly Plus 2PM
    "PlusI4": ("i4", "", "", 2),                      # Shelly Plus i4
    "PlusPlugS": ("Plug", "", "S", 2),                # Shelly Plus Plug S
    "PlusPMMini": ("PM", "Mini", "", 2),              # Shelly Plus PM Mini
    "Mini1G3": ("1", "Mini", "", 3),                  # Shelly 1 Mini Gen3
    "Mini1PMG3": ("1PM", "Mini", "", 3),              # Shelly 1PM Mini Gen3
    "S1G3": ("1", "", "", 3),                         # Shelly 1 Gen3
    "S1PMG3": ("1PM", "", "", 3),                     # Shelly 1PM Gen3
    "S2PMG3": ("2PM", "", "", 3),                     # Shelly 2PM Gen3
    "I4G3": ("i4", "", "", 3),                        # Shelly i4 Gen3
    "S1G4": ("1", "", "", 4),                         # Shelly 1 Gen4
    "S1PMG4": ("1PM", "", "", 4),                     # Shelly 1PM Gen4
    "S2PMG4": ("2PM", "", "", 4),                     # Shelly 2PM Gen4
}


def _fallback(app: str) -> tuple[str, int | None]:
    """Name from the firmware family of an unknown device, e.g. "ProDimmer1PM" -> "Shelly Pro Dimmer 1PM",
    "Foo2PMG4" -> ("Shelly Foo 2PM", 4)."""
    gen = None
    m = re.search(r"G(\d)$", app)
    if m:
        gen, app = int(m.group(1)), app[:m.start()]
    return "Shelly " + re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", app), gen


def device_type(model: str | None, app: str | None = None, gen: Any = None) -> dict[str, Any]:
    """Kind, variant, suffix and generation of a device plus the display strings: `type_name` (first line),
    `type_sub` (second line) and `model_name` (one line, for group headings). `gen` from `/shelly` wins over
    the table. Unknown devices get kind `unknown`."""
    hit = MODELS.get(model.upper()) if model else None
    hit = hit or (APPS.get(app) if app else None)
    if hit:
        kind, variant, suffix, table_gen = hit
        name = f"Shelly {kind}"
    else:
        kind, variant, suffix = "unknown", "", ""
        name, table_gen = _fallback(app) if app else (model or "Shelly", None)
    try:
        g = int(gen) if gen not in (None, "") else table_gen
    except (TypeError, ValueError):
        g = table_gen
    sub = " · ".join(x for x in (f"Gen{g}" if g else "", variant, suffix) if x)
    return {"kind": kind, "variant": variant or "Standard", "suffix": suffix, "gen": g,
            "type_name": name, "type_sub": sub, "model_name": f"{name} · {sub}" if sub else name}


def type_fields(d: dict[str, Any]) -> dict[str, Any]:
    """Type fields for a device row in the API: kind, variant, suffix, type_name, type_sub, model_name."""
    t = device_type(d.get("model"), d.get("app"), d.get("gen"))
    t.pop("gen")
    return t


def model_name(model: str | None, app: str | None = None, gen: Any = None) -> str:
    """One-line type label for group headings and lists, e.g. `Shelly 2PM · Gen2 · Pro`; never empty."""
    return device_type(model, app, gen)["model_name"]


def format_mac(mac: str | None) -> str | None:
    """`1020BA000201`, `10-20-ba-00-02-01` etc. -> `10:20:BA:00:02:01`. Anything unusable stays unchanged."""
    if not mac:
        return mac
    digits = re.sub(r"[^0-9A-Fa-f]", "", mac)
    if len(digits) != 12:
        return mac
    digits = digits.upper()
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2))
