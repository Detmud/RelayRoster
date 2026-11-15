"""Readable product names for Shelly model identifiers and MAC addresses in the usual format.

`Shelly.GetDeviceInfo` returns `model` (order number like `S4SW-002P16EU`) and `app` (firmware family
like `Plus1`). The UI shows the name under which Shelly sells the device instead
(`Shelly 2PM Gen4`). Unknown identifiers fall back to a form derived from `app`, the raw values
stay visible in the details.
"""
from __future__ import annotations

import re

# Order number -> product name. Country/variant suffixes (EU, UK, US …) are listed individually,
# because Shelly does not form them consistently.
MODEL_NAMES: dict[str, str] = {
    # Plus (Gen2)
    "SNSW-001X16EU": "Shelly Plus 1",
    "SNSW-001X8EU": "Shelly Plus 1 Mini",
    "SNSW-001P16EU": "Shelly Plus 1PM",
    "SNSW-001P8EU": "Shelly Plus 1PM Mini",
    "SNSW-002P16EU": "Shelly Plus 2PM",
    "SNSW-102P16EU": "Shelly Plus 2PM",
    "SNSN-0024X": "Shelly Plus i4",
    "SNSN-0D24X": "Shelly Plus i4 DC",
    "SNPL-00112EU": "Shelly Plus Plug S",
    "SNPL-10112EU": "Shelly Plus Plug S",
    "SNPL-00110IT": "Shelly Plus Plug IT",
    "SNPL-00112UK": "Shelly Plus Plug UK",
    "SNPL-00116US": "Shelly Plus Plug US",
    "SNPM-001PCEU16": "Shelly Plus PM Mini",
    "SNSN-0013A": "Shelly Plus H&T",
    "SNSN-0031Z": "Shelly Plus Smoke",
    "SNDM-0013US": "Shelly Plus Wall Dimmer",
    # Pro (Gen2)
    "SPSW-001XE16EU": "Shelly Pro 1",
    "SPSW-201XE16EU": "Shelly Pro 1",
    "SPSW-001PE16EU": "Shelly Pro 1PM",
    "SPSW-201PE16EU": "Shelly Pro 1PM",
    "SPSW-002XE16EU": "Shelly Pro 2",
    "SPSW-202XE16EU": "Shelly Pro 2",
    "SPSW-002PE16EU": "Shelly Pro 2PM",
    "SPSW-202PE16EU": "Shelly Pro 2PM",
    "SPSW-003XE16EU": "Shelly Pro 3",
    "SPSW-004PE16EU": "Shelly Pro 4PM",
    "SPSW-104PE16EU": "Shelly Pro 4PM",
    "SPEM-003CEBEU": "Shelly Pro 3EM",
    # Gen3
    "S3SW-001X16EU": "Shelly 1 Gen3",
    "S3SW-001P16EU": "Shelly 1PM Gen3",
    "S3SW-001X8EU": "Shelly 1 Mini Gen3",
    "S3SW-001P8EU": "Shelly 1PM Mini Gen3",
    "S3SW-002P16EU": "Shelly 2PM Gen3",
    "S3PM-001PCEU16": "Shelly PM Mini Gen3",
    "S3SN-0024X": "Shelly i4 Gen3",
    "S3SN-0U12A": "Shelly H&T Gen3",
    # Gen4
    "S4SW-001X16EU": "Shelly 1 Gen4",
    "S4SW-001P16EU": "Shelly 1PM Gen4",
    "S4SW-001X8EU": "Shelly 1 Mini Gen4",
    "S4SW-001P8EU": "Shelly 1PM Mini Gen4",
    "S4SW-002P16EU": "Shelly 2PM Gen4",
}

# Firmware family (`app`) -> product name, if the order number is unknown
APP_NAMES: dict[str, str] = {
    "Plus1": "Shelly Plus 1",
    "Plus1Mini": "Shelly Plus 1 Mini",
    "Plus1PM": "Shelly Plus 1PM",
    "Plus1PMMini": "Shelly Plus 1PM Mini",
    "Plus2PM": "Shelly Plus 2PM",
    "PlusI4": "Shelly Plus i4",
    "PlusPlugS": "Shelly Plus Plug S",
    "PlusPMMini": "Shelly Plus PM Mini",
    "Mini1G3": "Shelly 1 Mini Gen3",
    "Mini1PMG3": "Shelly 1PM Mini Gen3",
    "S1G3": "Shelly 1 Gen3",
    "S1PMG3": "Shelly 1PM Gen3",
    "S2PMG3": "Shelly 2PM Gen3",
    "I4G3": "Shelly i4 Gen3",
    "S1G4": "Shelly 1 Gen4",
    "S1PMG4": "Shelly 1PM Gen4",
    "S2PMG4": "Shelly 2PM Gen4",
}


def model_name(model: str | None, app: str | None = None) -> str:
    """Sales name for an order number or firmware family; never empty."""
    if model and model.upper() in MODEL_NAMES:
        return MODEL_NAMES[model.upper()]
    if app and app in APP_NAMES:
        return APP_NAMES[app]
    if app:
        # z.B. "ProDimmer1PM" -> "Shelly Pro Dimmer 1PM", "XyzG3" -> "Shelly Xyz Gen3"
        name = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", app)
        name = re.sub(r"\s*G(\d)$", r" Gen\1", name)
        return f"Shelly {name}"
    return model or "Shelly"


def format_mac(mac: str | None) -> str | None:
    """`1020BA000201`, `10-20-ba-00-02-01` etc. -> `10:20:BA:00:02:01`. Anything unusable stays unchanged."""
    if not mac:
        return mac
    digits = re.sub(r"[^0-9A-Fa-f]", "", mac)
    if len(digits) != 12:
        return mac
    digits = digits.upper()
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2))
