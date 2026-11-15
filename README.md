<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <img src="docs/assets/logo-light.svg" alt="RelayRoster" height="64">
  </picture>
</h1>

**Local fleet management for Shelly Gen2+ devices** (Plus, Pro, Gen3, Gen4).

RelayRoster finds the Shelly devices in your network, keeps a versioned backup of their configuration and shows
where they deviate from the state you want, or from each other. Everything stays in your network: no cloud, no account.

> Shelly is a trademark of Shelly Europe Ltd. RelayRoster is an independent project and is not affiliated with,
> endorsed by or sponsored by Shelly Europe Ltd.

## Features

- **Inventory**: all devices with model, firmware, address, Wi-Fi signal and reachability
- **Drift detection**: compare every device against a target policy you define in YAML or in the UI
- **Peer comparison**: spot devices that are configured differently from others of the same type
- **Backups**: versioned configuration snapshots with diff, download and restore
- **Firmware**: see which devices have updates and roll them out
- **Bulk changes**: change one setting on many devices at once

**Read-only by default.** Every write function (configuration, firmware updates, restarts, device passwords) has
its own switch and is off until you turn it on. Every write is recorded in an audit log.

## Quick start

You need Docker with Compose and a host in the same network as your devices.

```bash
mkdir relayroster && cd relayroster
curl -fsSLO https://raw.githubusercontent.com/Detmud/RelayRoster/main/compose.yaml
curl -fsSL  https://raw.githubusercontent.com/Detmud/RelayRoster/main/.env.example -o .env
nano .env                   # set SCAN_NETWORKS to your network, e.g. 192.168.1.0/24
docker compose up -d
```

Open `http://<host>:8746`.

> [!WARNING]
> This quick start has **no sign-in**: anyone who can reach port 8746 can use the app. Fine for a first look in your
> own network. For permanent use, set up sign-in as described in [Installation](docs/installation.md#run-it-permanently).

## Documentation

| Topic | |
|---|---|
| [Installation](docs/installation.md) | Permanent setup with sign-in, image tags, updates, backups, building from source |
| [Configuration](docs/configuration.md) | Environment variables and the Settings page |
| [Target policy](docs/policy.md) | Baseline, profiles, overrides, address and naming plan |
| [Write functions](docs/write-functions.md) | What RelayRoster can change on devices and how it stays safe |
| [Security](docs/security.md) | Sign-in via OIDC and the security model |
| [User interface](docs/user-interface.md) | The pages of the app |
| [Development](docs/development.md) | Running the tests and the demo with a simulated fleet |

## Contributing

Issues and pull requests are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md). The tests run without real devices:
`bash tests/ci.sh`. Security problems go to [SECURITY.md](SECURITY.md), not to a public issue.

RelayRoster is a hobby project. Write functions change real devices; use them at your own risk, as the
[licence](LICENSE) says.

## License

[MIT](LICENSE). RelayRoster is built with [Tabler](https://tabler.io) and [Tabler Icons](https://tabler.io/icons);
all third-party licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
