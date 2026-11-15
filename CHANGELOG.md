# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
