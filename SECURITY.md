# Security policy

RelayRoster talks to every Shelly device in your network, stores device passwords and, when you enable the write
functions, changes configuration, firmware and passwords on those devices. Please take security reports seriously
and report them privately.

## Reporting a vulnerability

**Do not open a public issue for security problems.**

Report them through GitHub's private vulnerability reporting: open the repository's *Security* tab and choose
*Report a vulnerability*. You will get a reply within 7 days. Please include the version (`Settings` page or the
image tag), the steps to reproduce and what an attacker could gain.

RelayRoster is a hobby project maintained in spare time. There is no bug bounty, but every report is read and
credited in the changelog if you want.

## Supported versions

| Version | Supported |
|---|---|
| latest 1.x release | yes |
| older releases | no, please update |

Fixes are released as a new patch version on `ghcr.io/detmud/relayroster`; the `1` tag always points to it.

## What counts as a vulnerability

- Reaching device data or the write functions without being signed in when `OIDC_ISSUER` is set
- Cross-site request forgery against the write functions
- Reading device passwords or OIDC secrets through the API or the UI
- Writing to a device field that is not in the allow list in `app/writable.py`
- Path traversal in backups, downloads or restores

Running RelayRoster **without** sign-in (no `OIDC_ISSUER`) deliberately exposes everything to anyone who can reach
the port. That is documented in [docs/security.md](docs/security.md) and the app warns about it; it is not a
vulnerability.
