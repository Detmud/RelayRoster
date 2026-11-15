# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [Semantic Versioning](https://semver.org/).

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
