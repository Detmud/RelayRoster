# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

- Dashboard: "Device types" shows the type as in the device list, the function first and generation and form
  factor in the line below, instead of one long name that wrapped
- Dashboard: the tile "Peer differences" says "All consistent" at 0, in line with the other tiles
- Device overview: the policy profiles of a device are a card of their own (one row per profile, with where it
  applies from, "Remove" and "Add to profile") instead of chips and a select inside the device data. The four
  cards are of similar length; the empty "Profile" field is gone and is called "Device mode" where a device has one
- Policy editor shows line numbers and the position of the cursor. A YAML error names its line first, marks it
  beside the text and jumps there with a click; long lines scroll sideways instead of wrapping
- The hint "rule could match by function" no longer appears for a rule that picks only some devices of a kind
  (for example a list of IDs for one purpose): `kind` would match the others too

- Firmware updates of several devices run at the same time, four by default ("Parallel firmware updates" in
  Settings, `FIRMWARE_PARALLEL`; 1 keeps the old one-after-the-other). The update dialog shows the step of every
  device: check, request, install, restart, verify, with the seconds each step began

- **Apply address plan** (new write function, off by default, switch "Allow address changes" / `ALLOW_ADDRESS`):
  devices outside their address block get the next free address of the block as a static address on the device.
  Preview with editable address, one device after another, check that the address is free, wait for the device at
  the new address and compare its ID; no rollback. `network` in the policy accepts `gateway` and `nameserver` for
  devices that are on DHCP

- Policy: `match` in profiles and address blocks accepts `kind` (what the hardware does: `2PM`, `i4`, `Plug`) and
  `variant` (`Standard`, `Pro`, `Mini`), so one rule covers all generations and survives firmware updates that
  rename device IDs. The default policy matches "Button input" by `kind: i4`. Findings shows a hint for rules that
  still match by a glob on `app` or `id` where `kind` would do; existing policies are not changed
- Device list: the type filter also offers function, generation and form factor

- Peer comparison: "Use majority" writes the majority value to the differing devices through the usual preview,
  optionally with a rule in the policy; "Set…" opens Bulk change with the setting and the type filled in. Needs
  "Allow config changes"

- Findings and the peer comparison show a readable name per setting ("Switch 0 · Auto off", "Password
  protection") and the note what is wrong; the path follows below in small type. The API adds `label` to findings
  and peer rows

- Dialogs close in one way: Cancel or Close in the footer (or Esc); no X in the header and no closing by a click
  next to the dialog. Confirmations are dialogs of the app with a named button instead of the browser's own

- Dashboard: the tile "Firmware updates" is now "Updates" and opens the device list filtered by "Update available"

- Every table can be sorted by a click on a column heading (ascending, descending, back to the default order);
  pages remember the sort per table
- Dashboard: "Needs attention" shows the address as first column, links it to the device and can be sorted;
  "Device types" can be sorted

- Dates, times and numbers follow the browser's locale and time zone, with a four-digit year and without "at";
  log tables (Activity, Backups) show seconds. Every time has the full ISO 8601 time with offset as tooltip

- "Audit log" is now called **Activity** (`#activity`; `#audit` still works). The CSV export is named
  `relayroster-activity.csv`; the API path `/api/audit` is unchanged
- The product name is written "RelayRoster" everywhere in the UI
- Back to Tabler's neutral greys for the page background, cards, borders and text, and to Tabler's plain active
  navigation; the navy tint and the marker bar from 1.0.2 are gone. Navy stays for buttons and links

## [1.0.2] - 2026-11-15

- Device types show the function first: "Shelly 2PM" with "Gen4" or "Gen2 · Pro" below instead of "Shelly 2PM Gen4"
  or "Shelly Pro 2PM". "Plus" is shown as Gen2; Pro and Mini as form factor; DC and the plug types as suffix.
  Group headings (firmware, peer comparison, bulk change) read "Shelly 2PM · Gen4". The order number is in the
  tooltip, the device details and the search. The API adds `kind`, `variant`, `suffix`, `type_name` and `type_sub`
- Navigation: "Roster" instead of the old "Fleet"; Bulk change moved to Roster; "Sync now" only on pages with
  device data
- Findings: columns line up across devices; no repeated source text next to the badge
- New colour palette for light and dark mode (see docs/assets/BRAND.md)

## [1.0.1] - 2026-11-15

- Trademark notice in the footer of the app and on the sign-in page
- Image based on Python 3.15; CI tests on the same version
- FastAPI 0.162.5
- Community files: CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md, issue and pull request templates

## [1.0.0] - 2026-11-15

First public release.

- Discovery by HTTP scan, periodic reads of all Shelly Gen2+ devices
- Target policy (`policy.yaml`) with baseline, profiles and overrides, address plan and naming plan
- Peer comparison of devices of the same type, without any policy
- Versioned configuration backups with diff, download and restore
- Firmware overview and updates (stable and beta)
- Write functions behind individual switches, all off by default: edit configuration, apply profiles, bulk change,
  rename, restart, set device password
- Audit log for every write action, CSV export
- Sign-in via OIDC
- Container images for amd64 and arm64 on `ghcr.io/detmud/relayroster`
