# User interface

## Pages

| Page | Content |
|---|---|
| Dashboard | Tiles for devices, policy deviations, peer differences, firmware updates and unreachable devices; each opens the matching filtered view |
| Devices | All devices with type (function first, e.g. "Shelly 2PM", with generation, Pro/Mini and suffix below), MAC, address, firmware, Wi-Fi signal, matching profiles, deviations, reachability. Sortable and filterable; the view is part of the URL |
| Device page | `#device/<id>[/<tab>]`: overview, settings per component (editable), findings, backups, changes (activity) |
| Findings | Target vs. actual from `policy.yaml`, per device with path, target, actual value and the rule that applies |
| Peer comparison | Devices of the same type compared with each other, **without** any policy. Shows every effective setting that is not the same on all of them and marks the outliers |
| Firmware | Installed version per type, mixed versions, stable and beta updates reported by the devices; update per device, per type or all |
| Backups | Versioned snapshots per device (configuration, scripts with code, schedules, webhooks, KVS), diff to the previous one, download as JSON or everything as ZIP, restore |
| Bulk change | One setting for many devices at once (e.g. the NTP server for all devices), restart several devices, roll out a device password |
| Policy | `#policy`: **Rules** edits the baseline and creates, edits, orders and deletes profiles without YAML, with live preview, and applies a profile to its devices; **Overrides** lists the device overrides; **YAML** is the editor for the whole `policy.yaml` with validation before saving |
| Activity | Everything RelayRoster wrote (the audit log), exportable as CSV; `#audit` still opens it |
| Settings | Networks, intervals, timeouts, device password and the write switches; stored in `/data`, effective immediately |

A new snapshot is only stored when the configuration actually changed. A firmware update or an increased `cfg_rev`
alone does not create one.

## Styles and icons

The UI is built with [Tabler](https://tabler.io) by Paweł Kuna and the Tabler Authors (MIT): the CSS of
[`@tabler/core`](https://github.com/tabler/tabler) and individual [Tabler Icons](https://tabler.io/icons) as inline
SVG. No Tabler JS and no build step. The CSS is not in the
repository: `tools/fetch_vendor.py` downloads the official npm package `@tabler/core`, checks it against a pinned
SHA-256 and places `tabler.min.css` together with its license text in `app/static/vendor/`. In `docker build` this runs in its own stage; a wrong
hash fails the build. At runtime the browser loads nothing from the internet. Dark mode follows the system setting.
